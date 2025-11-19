# api_execute_stream.py
import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .telemetry_context import current_run_ctx, RunTelemetryContext, TelemetryWriter
from .http_client import http_client  # ensures hooks are registered
from .langgraph_app import app        # your compiled LangGraph app

router = APIRouter()


def _sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


@router.post("/v1/execute/stream")
async def execute_stream(request: Request):
    tenant_id = request.headers.get("X-Tenant-Id", "unknown")
    correlation_id = request.headers.get("X-Correlation-Id", str(uuid.uuid4()))
    thread_id = request.headers.get("X-Thread-Id", str(uuid.uuid4()))
    run_id = str(uuid.uuid4())

    body = await request.json()
    messages = body.get("messages", [])

    initial_state: dict[str, Any] = {
        "messages": messages,
        "tenant_id": tenant_id,
        "thread_id": thread_id,
    }

    # Queue for httpx telemetry + any other out-of-band telemetry
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def telemetry_writer(event: dict) -> None:
        """
        Called from httpx hooks & possibly from other non-LangGraph code.
        """
        # decorate with standard fields if they’re missing
        event.setdefault("run_id", run_id)
        event.setdefault("tenant_id", tenant_id)
        event.setdefault("thread_id", thread_id)
        event.setdefault("correlation_id", correlation_id)
        queue.put_nowait(event)

    async def event_generator():
        # Set the run context so httpx hooks can see it
        ctx: RunTelemetryContext = {
            "tenant_id": tenant_id,
            "thread_id": thread_id,
            "run_id": run_id,
            "writer": telemetry_writer,
        }
        token = current_run_ctx.set(ctx)

        try:
            # LangGraph built-in streaming
            async for mode, chunk in app.astream(
                initial_state,
                config={
                    "configurable": {
                        "tenant_id": tenant_id,
                        "thread_id": thread_id,
                        "run_id": run_id,
                        "correlation_id": correlation_id,
                    }
                },
                stream_mode=["messages", "updates", "custom"],
            ):
                # 1) Flush any pending httpx telemetry
                while not queue.empty():
                    telem = await queue.get()
                    yield _sse(
                        "response.telemetry.delta",
                        {
                            "channel": "http",
                            "type": telem.get("kind", "telemetry"),
                            **telem,
                        },
                    )

                # 2) Handle built-in LangGraph streaming modes
                if mode == "messages":
                    # chunk = (Message, metadata)
                    message, mdata = chunk
                    if message.content:
                        yield _sse(
                            "response.output_text.delta",
                            {
                                "channel": "llm",
                                "tenant_id": tenant_id,
                                "thread_id": thread_id,
                                "run_id": run_id,
                                "correlation_id": correlation_id,
                                "node": mdata.get("langgraph_node"),
                                "content": message.content,
                            },
                        )

                elif mode == "updates":
                    yield _sse(
                        "response.state.delta",
                        {
                            "tenant_id": tenant_id,
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "correlation_id": correlation_id,
                            "updates": chunk,
                        },
                    )

                elif mode == "custom":
                    # This is what you emit via LangGraph's StreamWriter in nodes
                    yield _sse(
                        "response.telemetry.delta",
                        {
                            "channel": "graph",
                            "tenant_id": tenant_id,
                            "thread_id": thread_id,
                            "run_id": run_id,
                            "correlation_id": correlation_id,
                            **chunk,
                        },
                    )

            # after the LangGraph stream completes, flush any remaining telemetry
            while not queue.empty():
                telem = await queue.get()
                yield _sse(
                    "response.telemetry.delta",
                    {
                        "channel": "http",
                        "type": telem.get("kind", "telemetry"),
                        **telem,
                    },
                )

            # final completion event
            yield _sse(
                "response.completed",
                {
                    "tenant_id": tenant_id,
                    "thread_id": thread_id,
                    "run_id": run_id,
                    "correlation_id": correlation_id,
                },
            )

        finally:
            current_run_ctx.reset(token)

    return StreamingResponse(event_generator(), media_type="text/event-stream")
