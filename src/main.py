"""FastAPI application entrypoint."""

from fastapi import FastAPI

from .api import api_router
from .core.middleware import configure_core_middleware

app = FastAPI(title="Codeless Orchestration API", version="0.1.0")
app.include_router(api_router)
configure_core_middleware(app)


@app.get("/healthz", tags=["health"])
async def health_check() -> dict[str, str]:
    """Lightweight health probe."""
    return {"status": "ok"}
