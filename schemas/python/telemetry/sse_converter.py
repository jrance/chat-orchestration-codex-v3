"""
sse_converter.py

Convert LangGraph `astream_events` into OpenAI Responses-style SSE events.

Usage (inside your FastAPI SSE endpoint):

    from sse_converter import (
        RunContext,
        run_graph_and_stream_openai_sse,
    )

    @router.post("/v1/execute/stream")
    async def execute_stream(request: Request):
        body = await request.json()
        messages = body["messages"]

        ctx = RunContext(
            tenant_id=request.headers.get("X-Tenant-Id", "unknown"),
            thread_id=request.headers.get("X-Thread-Id", str(uuid.uuid4())),
            run_id=str(uuid.uuid4()),
            correlation_id=request.headers.get("X-Correlation-Id", str(uuid.uuid4())),
            model=body.get("model", "gpt-4.1"),
        )

        initial_input = {"messages": messages}

        async def event_generator():
            async for frame in run_graph_and_stream_openai_sse(
                app,                      # your compiled LangGraph app
                initial_input,
                ctx,
                config={},                # optional LangGraph config
                http_telemetry_queue=None # or an asyncio.Queue[dict] from httpx hooks
            ):
                yield frame

        return StreamingResponse(event_generator(), media_type="text/event-stream")
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Optional


# ---------------------------------------------------------------------------
# Context / state types
# ---------------------------------------------------------------------------


@dataclass
class RunContext:
    """Run-level context stamped onto all SSE events."""

    tenant_id: str
    thread_id: str
    run_id: str
    correlation_id: str
    model: str
    # Optional extra metadata that you want to attach to the response
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResponseStreamState:
    """Holds state needed to build Responses-style streaming envelopes."""

    response_id: str
    item_id: str
    created_at: int
    sequence_number: int = 0
    text_buffer: list[str] = field(default_factory=list)
    output_started: bool = False
    content_part_started: bool = False
    last_node: Optional[str] = None  # for handoff detection

    def next_seq(self) -> int:
        self.sequence_number += 1
        return self.sequence_number


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_sse(event: str, data: Dict[str, Any]) -> str:
    """
    Format an SSE frame: event name + JSON data.

    We use the OpenAI Responses convention:
      event: response.output_text.delta
      data: { "type": "response.output_text.delta", ... }
    """
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n"


def _base_metadata(ctx: RunContext) -> Dict[str, Any]:
    """Metadata object attached to response/item where appropriate."""
    return {
        "tenant_id": ctx.tenant_id,
        "thread_id": ctx.thread_id,
        "run_id": ctx.run_id,
        "correlation_id": ctx.correlation_id,
        **(ctx.metadata or {}),
    }


def _init_stream_state(ctx: RunContext) -> ResponseStreamState:
    now = int(time.time())
    response_id = f"resp_{ctx.run_id}"
    item_id = f"msg_{uuid.uuid4().hex}"
    return ResponseStreamState(
        response_id=response_id,
        item_id=item_id,
        created_at=now,
    )


def _extract_text_from_chunk(chunk: Any) -> str:
    """
    Best-effort extraction of text from a LangGraph `on_chat_model_stream` chunk.

    This handles a few common cases:
      - plain strings
      - dicts with `content` (string or list of parts)
      - langchain-style message chunks with `.content`
    You can customize this for your app if needed.
    """
    if chunk is None:
        return ""

    # AIMessageChunk, etc., with .content
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content

    if isinstance(chunk, str):
        return chunk

    if isinstance(chunk, dict):
        content = chunk.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, dict):
                    text = part.get("text") or part.get("delta")
                    if isinstance(text, str):
                        parts.append(text)
            return "".join(parts)

    # Fallback
    return str(chunk)


# ---------------------------------------------------------------------------
# Emit OpenAI Responses SSE events
# ---------------------------------------------------------------------------


def _event_response_created(
    ctx: RunContext, state: ResponseStreamState
) -> str:
    event_type = "response.created"
    payload = {
        "type": event_type,
        "response": {
            "id": state.response_id,
            "object": "response",
            "created_at": state.created_at,
            "status": "in_progress",
            "model": ctx.model,
            "metadata": _base_metadata(ctx),
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_response_in_progress(state: ResponseStreamState) -> str:
    event_type = "response.in_progress"
    payload = {
        "type": event_type,
        "response": {
            "id": state.response_id,
            "status": "in_progress",
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_output_item_added(ctx: RunContext, state: ResponseStreamState) -> str:
    event_type = "response.output_item.added"
    payload = {
        "type": event_type,
        "output_index": 0,
        "item": {
            "id": state.item_id,
            "status": "in_progress",
            "type": "message",
            "role": "assistant",
            "content": [],
            "metadata": _base_metadata(ctx),
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_content_part_added(state: ResponseStreamState) -> str:
    event_type = "response.content_part.added"
    payload = {
        "type": event_type,
        "item_id": state.item_id,
        "output_index": 0,
        "content_index": 0,
        "part": {
            "type": "output_text",
            "text": "",
            "annotations": [],
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_output_text_delta(
    delta_text: str, state: ResponseStreamState
) -> str:
    event_type = "response.output_text.delta"
    payload = {
        "type": event_type,
        "item_id": state.item_id,
        "output_index": 0,
        "content_index": 0,
        "delta": delta_text,
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_content_part_done(
    full_text: str, state: ResponseStreamState
) -> str:
    event_type = "response.content_part.done"
    payload = {
        "type": event_type,
        "item_id": state.item_id,
        "output_index": 0,
        "content_index": 0,
        "part": {
            "type": "output_text",
            "text": full_text,
            "annotations": [],
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_output_text_done(
    full_text: str, state: ResponseStreamState
) -> str:
    event_type = "response.output_text.done"
    payload = {
        "type": event_type,
        "item_id": state.item_id,
        "output_index": 0,
        "content_index": 0,
        "text": full_text,
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_output_item_done(
    ctx: RunContext, full_text: str, state: ResponseStreamState
) -> str:
    event_type = "response.output_item.done"
    payload = {
        "type": event_type,
        "output_index": 0,
        "item": {
            "id": state.item_id,
            "status": "completed",
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "output_text",
                    "text": full_text,
                    "annotations": [],
                }
            ],
            "metadata": _base_metadata(ctx),
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_response_completed(
    ctx: RunContext, full_text: str, state: ResponseStreamState
) -> str:
    event_type = "response.completed"
    payload = {
        "type": event_type,
        "response": {
            "id": state.response_id,
            "status": "completed",
            "model": ctx.model,
            "output": [
                {
                    "type": "message",
                    "id": state.item_id,
                    "status": "completed",
                    "role": "assistant",
                    "content": [
                        {
                            "type": "output_text",
                            "text": full_text,
                            "annotations": [],
                        }
                    ],
                    "metadata": _base_metadata(ctx),
                }
            ],
            "usage": {
                # You can fill this in with real numbers if you track them
                "input_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0},
                "output_tokens": 0,
                "output_tokens_details": {"reasoning_tokens": 0},
                "total_tokens": 0,
            },
            "metadata": _base_metadata(ctx),
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


def _event_reasoning_delta(
    ctx: RunContext,
    state: ResponseStreamState,
    *,
    delta_text: str,
    from_node: Optional[str],
    to_node: Optional[str],
    extra: Optional[Dict[str, Any]] = None,
) -> str:
    """
    "Thinking" / reasoning event that UIs can render as a timeline.

    This uses the official `response.reasoning.delta` type, with:
      - delta: human-readable text
      - metadata: from_node/to_node + run/thread info
    """
    event_type = "response.reasoning.delta"
    payload: Dict[str, Any] = {
        "type": event_type,
        "response_id": state.response_id,
        "output_index": 0,
        "delta": delta_text,
        "metadata": {
            "thread_id": ctx.thread_id,
            "run_id": ctx.run_id,
            "tenant_id": ctx.tenant_id,
            "correlation_id": ctx.correlation_id,
            "from_node": from_node,
            "to_node": to_node,
        },
        "sequence_number": state.next_seq(),
    }
    if extra:
        payload["metadata"].update(extra)
    return _format_sse(event_type, payload)


def _event_telemetry_delta(
    ctx: RunContext,
    state: ResponseStreamState,
    telemetry: Dict[str, Any],
    *,
    channel: str = "graph",
) -> str:
    """
    Structured telemetry event, e.g. HTTP calls, LLM requests, etc.

    Use `channel` to distinguish `graph` / `http` / `llm` / `tool` in the UI.
    """
    event_type = "response.telemetry.delta"
    payload = {
        "type": event_type,
        "response_id": state.response_id,
        "channel": channel,
        "telemetry": {
            **telemetry,
            "thread_id": ctx.thread_id,
            "run_id": ctx.run_id,
            "tenant_id": ctx.tenant_id,
            "correlation_id": ctx.correlation_id,
        },
        "sequence_number": state.next_seq(),
    }
    return _format_sse(event_type, payload)


# ---------------------------------------------------------------------------
# Core converter: LangGraph events -> OpenAI Responses SSE frames
# ---------------------------------------------------------------------------


async def langgraph_events_to_openai_sse(
    events: AsyncIterator[Dict[str, Any]],
    ctx: RunContext,
    *,
    http_telemetry_queue: Optional["asyncio.Queue[Dict[str, Any]]"] = None,
) -> AsyncIterator[str]:
    """
    Convert LangGraph `astream_events` output into OpenAI Responses-style SSE.

    - Emits response.* events (created, output_text.delta, completed)
    - Emits response.reasoning.delta for "thinking" / node handoffs
    - Emits response.telemetry.delta for structured telemetry
      (you can feed HTTP telemetry via http_telemetry_queue)
    """
    state = _init_stream_state(ctx)

    # Initial response lifecycle events
    yield _event_response_created(ctx, state)
    yield _event_response_in_progress(state)
    yield _event_output_item_added(ctx, state)
    yield _event_content_part_added(state)
    state.output_started = True
    state.content_part_started = True

    async for event in events:
        # Flush any out-of-band http telemetry (e.g. from httpx hooks)
        if http_telemetry_queue is not None:
            while not http_telemetry_queue.empty():
                telem = await http_telemetry_queue.get()
                yield _event_telemetry_delta(
                    ctx,
                    state,
                    telemetry=telem,
                    channel=telem.get("channel", "http"),
                )

        ev_type = event.get("event")
        data = event.get("data") or {}
        metadata = event.get("metadata") or {}
        node_name = metadata.get("langgraph_node") or event.get("name")

        # --- Node-level events -> thinking + handoff telemetry ---
        if ev_type == "on_chain_start" and node_name and node_name != "LangGraph":
            # Treat each node start as a "handoff" from the last node
            delta_text = (
                f"Handoff from '{state.last_node}' to '{node_name}'"
                if state.last_node
                else f"Entering node '{node_name}'"
            )
            triggers = metadata.get("langgraph_triggers") or ()
            extra = {
                "langgraph_triggers": triggers,
                "langgraph_path": metadata.get("langgraph_path"),
                "langgraph_step": metadata.get("langgraph_step"),
            }
            yield _event_reasoning_delta(
                ctx,
                state,
                delta_text=delta_text,
                from_node=state.last_node,
                to_node=node_name,
                extra=extra,
            )
            yield _event_telemetry_delta(
                ctx,
                state,
                telemetry={
                    "kind": "node.start",
                    "node": node_name,
                    "triggers": list(triggers),
                    "path": metadata.get("langgraph_path"),
                    "step": metadata.get("langgraph_step"),
                },
                channel="graph",
            )
            state.last_node = node_name
            continue

        if ev_type == "on_chain_end" and node_name and node_name != "LangGraph":
            yield _event_telemetry_delta(
                ctx,
                state,
                telemetry={
                    "kind": "node.end",
                    "node": node_name,
                    "path": metadata.get("langgraph_path"),
                    "step": metadata.get("langgraph_step"),
                },
                channel="graph",
            )
            continue

        # --- LLM events -> thinking + token stream ---
        if ev_type == "on_chat_model_start":
            delta_text = f"LLM call started in node '{node_name or 'unknown'}'"
            yield _event_reasoning_delta(
                ctx,
                state,
                delta_text=delta_text,
                from_node=state.last_node,
                to_node=node_name,
                extra={"phase": "llm_start"},
            )
            yield _event_telemetry_delta(
                ctx,
                state,
                telemetry={"kind": "llm.start", "node": node_name},
                channel="llm",
            )
            continue

        if ev_type == "on_chat_model_stream":
            chunk = data.get("chunk")
            text_delta = _extract_text_from_chunk(chunk)
            if text_delta:
                state.text_buffer.append(text_delta)
                yield _event_output_text_delta(text_delta, state)
            continue

        if ev_type == "on_chat_model_end":
            yield _event_telemetry_delta(
                ctx,
                state,
                telemetry={"kind": "llm.end", "node": node_name},
                channel="llm",
            )
            continue

        # --- Custom LangGraph `on_chain_stream` / custom telemetry ---
        # If you stream custom telemetry via `astream_events`, you can map it here.
        if ev_type == "on_chain_stream":
            # Example: assume data["chunk"] is your custom telemetry dict
            chunk = data.get("chunk")
            if isinstance(chunk, dict):
                yield _event_telemetry_delta(
                    ctx,
                    state,
                    telemetry={
                        "kind": "graph.stream",
                        **chunk,
                    },
                    channel="graph",
                )
            continue

        # You can optionally handle tool events when LangGraph exposes them:
        # if ev_type == "on_tool_start": ...
        # if ev_type == "on_tool_end": ...

    # After events are exhausted, finalize the response
    full_text = "".join(state.text_buffer)
    yield _event_content_part_done(full_text, state)
    yield _event_output_text_done(full_text, state)
    yield _event_output_item_done(ctx, full_text, state)
    yield _event_response_completed(ctx, full_text, state)

    # Optionally: a terminal [DONE] frame for clients that expect it
    yield "data: [DONE]\n\n"


# ---------------------------------------------------------------------------
# Convenience wrapper: run graph + convert to SSE
# ---------------------------------------------------------------------------


async def run_graph_and_stream_openai_sse(
    app: Any,
    initial_input: Dict[str, Any],
    ctx: RunContext,
    *,
    config: Optional[Dict[str, Any]] = None,
    events_version: str = "v2",
    http_telemetry_queue: Optional["asyncio.Queue[Dict[str, Any]]"] = None,
) -> AsyncIterator[str]:
    """
    Convenience entry-point:

    - calls `app.astream_events(...)`
    - feeds events into `langgraph_events_to_openai_sse`
    - returns an async iterator of SSE frames (strings)

    You can plug this directly into a FastAPI StreamingResponse.
    """
    if config is None:
        config = {}

    # Ensure LangGraph gets thread/run identifiers via `configurable`
    configurable = config.setdefault("configurable", {})
    configurable.setdefault("thread_id", ctx.thread_id)
    configurable.setdefault("run_id", ctx.run_id)
    configurable.setdefault("tenant_id", ctx.tenant_id)

    events = app.astream_events(
        input=initial_input,
        config=config,
        version=events_version,
    )

    async for frame in langgraph_events_to_openai_sse(
        events,
        ctx,
        http_telemetry_queue=http_telemetry_queue,
    ):
        yield frame
