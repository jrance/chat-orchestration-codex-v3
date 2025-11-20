# app/streaming/openai_responses_sse.py
"""
Convert LangGraph `.astream_events(...)` events into OpenAI Responses-style SSE.

Usage
-----

    from app.streaming.openai_responses_sse import (
        OpenAIResponsesSSEConverter,
        format_sse_event,
    )

    # Per-request (recommended) usage:
    converter = OpenAIResponsesSSEConverter(
        # Optional, helps tie events together:
        thread_id=thread_id,              # from your config["configurable"]["thread_id"]
        run_id=None,                      # will be populated from first event if omitted
        model="gpt-4.1-mini",             # optional; inferred from metadata if omitted
        metadata={"tenant_id": tenant_id, "correlation_id": correlation_id},
    )

    events = app.astream_events(
        input=graph_input,
        config=config,
        version="v2",
        stream_mode=["messages", "updates", "custom"],
    )

    async def sse_iterator():
        async for event in events:
            sse_chunk = converter.format_event(event)
            if sse_chunk:
                # sse_chunk may contain one or more SSE events separated by blank lines
                yield sse_chunk

        # Optionally ensure we emit response.completed / output_text.done
        final_chunk = converter.flush()
        if final_chunk:
            yield final_chunk


    # Simple global-style helper (less control, but matches the snippet you asked for):
    events = app.astream_events(
        input=graph_input,
        config=config,
        version="v2",
        stream_mode=["messages", "updates", "custom"],
    )

    async for event in events:
        # Uses a per-thread_id converter under the hood.
        # Always returns a string (possibly empty).
        yield format_sse_event(event)


Event mapping
-------------

* on_chat_model_stream
    -> response.output_text.delta

* on_chain_start / on_chain_end / on_tool_start / on_tool_end / on_retriever_*
    -> response.thinking.delta (for node + tool "thinking"/handoff info)

* on_chain_stream with custom payload from get_stream_writer
    - chunk["kind"] == "telemetry" or "telemetry" in chunk
        -> response.telemetry.delta
    - chunk["kind"] == "thinking" or "thinking" in chunk
        -> response.thinking.delta

* First "start" event (chain/graph/llm)
    -> response.created

* Finalization (flush() or appropriate end event)
    -> response.output_text.done + response.completed
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

try:
    # Optional, just for nicer isinstance checks on message chunks.
    from langchain_core.messages import BaseMessage, BaseMessageChunk  # type: ignore
except Exception:  # pragma: no cover - keep working even if not importable
    BaseMessage = object  # type: ignore
    BaseMessageChunk = object  # type: ignore


LangGraphEvent = Mapping[str, Any]


@dataclass
class _ResponsesStreamState:
    """Track OpenAI Responses-stream state for a single LangGraph run."""

    response_id: str
    item_id: str
    created_at: int
    model: Optional[str] = None
    thread_id: Optional[str] = None
    run_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    have_sent_created: bool = False
    have_sent_completed: bool = False

    output_index: int = 0       # For now, assume a single output (0)
    content_index: int = 0      # Increments with each token / content delta
    text_buffer: List[str] = field(default_factory=list)


def _to_sse(event_type: str, payload: Dict[str, Any], *, event_id: Optional[str] = None) -> str:
    """
    Build a single SSE event string.

    The SSE-spec form is:

        id: <event_id>        # optional
        event: <event_type>
        data: <json-payload>
        
        <blank line>
    """
    # Ensure type field is present in the JSON payload, mirroring OpenAI Responses
    # events like `{"type": "response.output_text.delta", ...}`
    if "type" not in payload:
        payload = dict(payload)
        payload["type"] = event_type

    lines: List[str] = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append("data: " + json.dumps(payload, separators=(",", ":")))
    # Blank line terminates the SSE event. Add one more newline for safety.
    return "\n".join(lines) + "\n\n"


def _extract_thread_id_from_metadata(metadata: Mapping[str, Any]) -> Optional[str]:
    tid = metadata.get("thread_id") or metadata.get("thread")
    return str(tid) if tid is not None else None


def _extract_model_from_event(event: LangGraphEvent) -> Optional[str]:
    """
    Try to infer the model name from event.metadata or data.input.

    LangGraph / LangChain typically attach model metadata under `ls_model_name`
    or `model` in the metadata. :contentReference[oaicite:4]{index=4}
    """
    metadata = event.get("metadata") or {}
    data = event.get("data") or {}

    model = (
        metadata.get("ls_model_name")
        or metadata.get("model")
        or metadata.get("model_name")
    )
    if model:
        return str(model)

    input_section = data.get("input") or {}
    if isinstance(input_section, dict):
        model = input_section.get("model") or input_section.get("model_name")
        if model:
            return str(model)

    return None


def _is_message_like(obj: Any) -> bool:
    """Heuristic check for LangChain message / message-chunk instances."""
    if isinstance(obj, (BaseMessage, BaseMessageChunk)):  # type: ignore[arg-type]
        return True
    # Fallback: duck-typing on `.content`
    return hasattr(obj, "content")


def _extract_text_from_chunk(chunk: Any) -> str:
    """
    Extract a text delta from a LangChain `AIMessageChunk` / message-like object.

    For `AIMessageChunk(content="hello")` this returns "hello".
    For list-of-block content, it concatenates the textual parts.
    """
    # Direct strings
    if isinstance(chunk, str):
        return chunk

    # Message / message-chunk style objects with `.content`
    content = getattr(chunk, "content", None)
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                txt = (
                    block.get("text")
                    or block.get("input_text")
                    or block.get("content")
                )
                if isinstance(txt, str):
                    parts.append(txt)
            else:
                txt = getattr(block, "text", None) or getattr(block, "content", None)
                if isinstance(txt, str):
                    parts.append(txt)
        return "".join(parts)

    # Fallback to string representation
    if content is not None:
        return str(content)

    return str(chunk)


def _safe_preview(value: Any, max_chars: int = 256) -> Any:
    """
    Create a compact preview of arbitrary data for thinking/telemetry events.

    - Short strings are returned as-is.
    - Long strings are truncated.
    - Dicts/lists are JSON-ified + truncated.
    """
    try:
        if isinstance(value, str):
            return value if len(value) <= max_chars else value[: max_chars - 3] + "..."
        # For structured data, pretty safe to just json-dump + truncate
        as_json = json.dumps(value, default=str)
        if len(as_json) <= max_chars:
            return json.loads(as_json)
        clipped = as_json[: max_chars - 3] + "..."
        return clipped
    except Exception:
        s = str(value)
        return s if len(s) <= max_chars else s[: max_chars - 3] + "..."


class OpenAIResponsesSSEConverter:
    """
    Stateful converter: maps LangGraph `astream_events` into Responses-style SSE.

    This is safe to use per FastAPI request (recommended). It maintains:
    - response_id, item_id
    - accumulated output_text (to emit `response.output_text.done` / `response.completed`)
    - current content_index, etc.
    """

    def __init__(
        self,
        *,
        thread_id: Optional[str] = None,
        run_id: Optional[str] = None,
        model: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        response_id: Optional[str] = None,
        item_id: Optional[str] = None,
    ) -> None:
        now = int(time.time())
        if response_id is None:
            response_id = "resp_" + uuid.uuid4().hex
        if item_id is None:
            item_id = "msg_" + uuid.uuid4().hex

        self._state = _ResponsesStreamState(
            response_id=response_id,
            item_id=item_id,
            created_at=now,
            model=model,
            thread_id=thread_id,
            run_id=run_id,
            metadata=dict(metadata or {}),
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def format_event(self, event: LangGraphEvent) -> str:
        """
        Convert a single LangGraph event into 0+ SSE events (as one string).

        Returns:
            str: "" if the event doesn't map to an SSE output,
                 or a string containing one or more SSE events separated by
                 blank lines.

        This method is idempotent across events for a single run, and is
        safe to call in:

            async for event in app.astream_events(...):
                yield converter.format_event(event)
        """
        state = self._state
        parts: List[str] = []

        event_type = event.get("event")
        metadata = event.get("metadata") or {}

        # Keep thread_id, run_id, model up-to-date, lazily filling them when we see events
        if not state.thread_id:
            state.thread_id = _extract_thread_id_from_metadata(metadata)

        if not state.model:
            model = _extract_model_from_event(event)
            if model:
                state.model = model

        if not state.run_id and event.get("run_id"):
            state.run_id = str(event["run_id"])

        # Attach LangGraph metadata to high-level response metadata (best-effort)
        if metadata and not state.metadata.get("langgraph"):
            state.metadata["langgraph"] = metadata

        # --------------------------------------------------------------
        # response.created (only once, on first "start"-ish event)
        # --------------------------------------------------------------
        if (
            not state.have_sent_created
            and event_type in ("on_chain_start", "on_graph_start", "on_chat_model_start")
        ):
            parts.append(self._build_response_created_sse(event))
            state.have_sent_created = True

        # --------------------------------------------------------------
        # LLM token stream -> response.output_text.delta
        # --------------------------------------------------------------
        if event_type == "on_chat_model_stream":
            sse = self._handle_chat_model_stream(event)
            if sse:
                parts.append(sse)

        # --------------------------------------------------------------
        # Custom / updates / values stream -> telemetry / thinking
        # --------------------------------------------------------------
        if event_type == "on_chain_stream":
            chain_parts = self._handle_chain_stream(event)
            if chain_parts:
                parts.extend(chain_parts)

        # --------------------------------------------------------------
        # Tool / node lifecycle -> thinking events
        # --------------------------------------------------------------
        if event_type in (
            "on_chain_start",
            "on_chain_end",
            "on_tool_start",
            "on_tool_end",
            "on_retriever_start",
            "on_retriever_end",
        ):
            label = None
            if event_type == "on_chain_start":
                label = "node_start"
            elif event_type == "on_chain_end":
                label = "node_end"
            elif event_type == "on_tool_start":
                label = "tool_start"
            elif event_type == "on_tool_end":
                label = "tool_end"
            elif event_type == "on_retriever_start":
                label = "retriever_start"
            elif event_type == "on_retriever_end":
                label = "retriever_end"

            if label:
                parts.append(self._build_thinking_sse(label, event))

        # --------------------------------------------------------------
        # Heuristic: emit completion events on graph/chain end
        # (you can also call flush() explicitly after the stream ends)
        # --------------------------------------------------------------
        if (
            not state.have_sent_completed
            and event_type in ("on_chain_end", "on_graph_end")
        ):
            completion_sse = self._build_completion_sse()
            if completion_sse:
                parts.append(completion_sse)
                state.have_sent_completed = True

        return "".join(parts)

    def flush(self) -> str:
        """
        Force emission of `response.output_text.done` + `response.completed`
        if they haven't already been sent.

        Intended to be called once after the `async for event in ...` loop ends.
        """
        if self._state.have_sent_completed:
            return ""

        sse = self._build_completion_sse()
        if sse:
            self._state.have_sent_completed = True
        return sse

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_response_created_sse(self, event: LangGraphEvent) -> str:
        state = self._state
        response_obj: Dict[str, Any] = {
            "id": state.response_id,
            "object": "response",
            "created_at": state.created_at,
            "model": state.model,
            "output": [],      # we only stream text; this can be filled client-side
            "metadata": state.metadata,
        }

        payload = {
            "response": response_obj,
        }
        # `type` will be injected by _to_sse as "response.created"
        return _to_sse("response.created", payload, event_id=state.response_id)

    def _handle_chat_model_stream(self, event: LangGraphEvent) -> str:
        data = event.get("data") or {}
        chunk = data.get("chunk")
        if chunk is None:
            return ""

        text = _extract_text_from_chunk(chunk)
        if not text:
            return ""

        state = self._state
        state.content_index += 1
        state.text_buffer.append(text)

        payload = {
            # `type` field auto-filled by _to_sse as "response.output_text.delta"
            "response_id": state.response_id,
            "item_id": state.item_id,
            "output_index": state.output_index,
            "content_index": state.content_index,
            "delta": text,
        }
        return _to_sse("response.output_text.delta", payload)

    def _handle_chain_stream(self, event: LangGraphEvent) -> List[str]:
        """
        Handle on_chain_stream events, focusing on:
        - Custom chunks emitted by get_stream_writer (telemetry / thinking).
        - Optional state updates for "updates"/"values" modes.
        """
        data = event.get("data") or {}
        chunk = data.get("chunk")
        if chunk is None:
            return []

        parts: List[str] = []
        state = self._state

        # Case 1: custom dict from LangGraph (e.g., get_stream_writer)
        if isinstance(chunk, dict):
            # Convention: writer({"kind": "telemetry", ...}) or writer({"telemetry": {...}})
            kind = chunk.get("kind") or chunk.get("type")
            if kind == "telemetry" or "telemetry" in chunk:
                telemetry = chunk.get("telemetry") or chunk
                payload = {
                    "response_id": state.response_id,
                    "run_id": state.run_id,
                    "thread_id": state.thread_id,
                    "telemetry": telemetry,
                }
                parts.append(_to_sse("response.telemetry.delta", payload))
            elif kind == "thinking" or "thinking" in chunk:
                thinking = chunk.get("thinking") or chunk
                payload = {
                    "response_id": state.response_id,
                    "run_id": state.run_id,
                    "thread_id": state.thread_id,
                    "thinking": thinking,
                }
                parts.append(_to_sse("response.thinking.delta", payload))
            # else: unknown custom payload; ignore by default (or add your own mapping)

        # Case 2: tuple from "values"/"updates"/other modes: (mode, value)
        elif (
            isinstance(chunk, tuple)
            and len(chunk) == 2
            and isinstance(chunk[0], str)
        ):
            mode, value = chunk
            if mode in ("values", "updates"):
                # Optional: surface graph state updates as a generic thinking event.
                payload = {
                    "response_id": state.response_id,
                    "run_id": state.run_id,
                    "thread_id": state.thread_id,
                    "mode": mode,
                    "state_preview": _safe_preview(value),
                }
                parts.append(_to_sse("response.thinking.delta", payload))
            elif mode in ("custom", "telemetry"):
                payload = {
                    "response_id": state.response_id,
                    "run_id": state.run_id,
                    "thread_id": state.thread_id,
                    "telemetry": _safe_preview(value),
                }
                parts.append(_to_sse("response.telemetry.delta", payload))

        # Other shapes are ignored by default
        return parts

    def _build_thinking_sse(self, label: str, event: LangGraphEvent) -> str:
        """
        Build a generic "thinking" event describing node/tool lifecycle or
        other reasoning steps that the UI can render as a thinking bubble.
        """
        state = self._state
        metadata = event.get("metadata") or {}
        payload = {
            "response_id": state.response_id,
            "run_id": state.run_id or event.get("run_id"),
            "thread_id": state.thread_id or _extract_thread_id_from_metadata(metadata),
            "label": label,                   # e.g., "tool_start", "node_end"
            "event": event.get("event"),
            "name": event.get("name"),
            "parent_ids": event.get("parent_ids") or [],
            "metadata": metadata,
        }
        return _to_sse("response.thinking.delta", payload)

    def _build_completion_sse(self) -> str:
        """
        Emit `response.output_text.done` followed by `response.completed`
        based on the accumulated text buffer. If there's no text at all,
        we still emit `response.completed` with an empty output.
        """
        state = self._state
        parts: List[str] = []

        full_text = "".join(state.text_buffer) if state.text_buffer else ""

        # output_text.done
        done_payload = {
            "response_id": state.response_id,
            "item_id": state.item_id,
            "output_index": state.output_index,
            "content_index": state.content_index,
            "output_text": full_text,
        }
        parts.append(_to_sse("response.output_text.done", done_payload))

        # response.completed with a minimal response object
        response_obj: Dict[str, Any] = {
            "id": state.response_id,
            "object": "response",
            "created_at": state.created_at,
            "model": state.model,
            "output": [
                {
                    "type": "output_text",
                    "output_text": {
                        "role": "assistant",
                        "content": full_text,
                    },
                }
            ],
            "metadata": state.metadata,
        }
        completed_payload = {"response": response_obj}
        parts.append(_to_sse("response.completed", completed_payload, event_id=state.response_id))

        return "".join(parts)


# ----------------------------------------------------------------------
# Simple global helper (matches your `yield format_sse_event(event)` call)
# ----------------------------------------------------------------------


_GLOBAL_CONVERTERS: Dict[str, OpenAIResponsesSSEConverter] = {}


def _get_global_converter_for_event(event: LangGraphEvent) -> OpenAIResponsesSSEConverter:
    """
    Get/create a converter keyed by thread_id (or run_id as fallback).

    This lets you use a "stateless-looking" API:

        async for event in events:
            yield format_sse_event(event)

    while still keeping per-run state (response_id, buffers, etc.).
    """
    metadata = event.get("metadata") or {}
    key = metadata.get("thread_id") or event.get("run_id") or "default"
    key = str(key)

    converter = _GLOBAL_CONVERTERS.get(key)
    if converter is None:
        converter = OpenAIResponsesSSEConverter(
            thread_id=_extract_thread_id_from_metadata(metadata),
            run_id=str(event.get("run_id")) if event.get("run_id") else None,
            model=_extract_model_from_event(event),
            metadata={"langgraph": metadata},
        )
        _GLOBAL_CONVERTERS[key] = converter
    return converter


def format_sse_event(event: LangGraphEvent) -> str:
    """
    Convenience wrapper suitable for:

        events = app.astream_events(...)
        async for event in events:
            yield format_sse_event(event)

    Always returns a string (possibly empty). It may contain *multiple* SSE
    events (e.g., output_text.done + response.completed) separated by blank
    lines; SSE clients handle this fine.
    """
    converter = _get_global_converter_for_event(event)
    return converter.format_event(event)


def reset_global_converters() -> None:
    """
    Optional helper to clear all global converters, e.g., in tests or
    between batches. Not strictly required if you're using per-request
    `OpenAIResponsesSSEConverter` instances instead.
    """
    _GLOBAL_CONVERTERS.clear()
