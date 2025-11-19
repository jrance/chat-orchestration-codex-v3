# http_client.py
import httpx
from .telemetry_context import current_run_ctx

def httpx_request_hook(request: httpx.Request) -> None:
    ctx = current_run_ctx.get()
    if ctx is None:
        return

    ctx["writer"]({
        "kind": "http.request",
        "run_id": ctx["run_id"],
        "tenant_id": ctx["tenant_id"],
        "thread_id": ctx["thread_id"],
        "method": request.method,
        "url": str(request.url),
    })

async def httpx_response_hook(response: httpx.Response) -> None:
    ctx = current_run_ctx.get()
    if ctx is None:
        return

    req = response.request
    ctx["writer"]({
        "kind": "http.response",
        "run_id": ctx["run_id"],
        "tenant_id": ctx["tenant_id"],
        "thread_id": ctx["thread_id"],
        "method": req.method,
        "url": str(req.url),
        "status_code": response.status_code,
    })

http_client = httpx.AsyncClient(
    timeout=10.0,
    event_hooks={
        "request": [httpx_request_hook],
        "response": [httpx_response_hook],
    },
)
