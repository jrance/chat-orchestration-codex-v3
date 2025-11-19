# telemetry_context.py
from contextvars import ContextVar
from typing import TypedDict, Protocol, Any

class TelemetryWriter(Protocol):
    def __call__(self, event: dict[str, Any]) -> None: ...

class RunTelemetryContext(TypedDict):
    tenant_id: str
    thread_id: str
    run_id: str
    writer: TelemetryWriter

current_run_ctx: ContextVar[RunTelemetryContext | None] = ContextVar(
    "current_run_ctx",
    default=None,
)
