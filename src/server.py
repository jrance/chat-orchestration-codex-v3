"""Simple uvicorn bootstrap for local development."""

from __future__ import annotations

import os

import uvicorn

from .main import app


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in {"1", "true", "on", "yes"}


def run() -> None:
    """Start the FastAPI application via uvicorn."""

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))
    reload_enabled = _env_flag("UVICORN_RELOAD", default=True)
    log_level = os.getenv("LOG_LEVEL", "info")

    uvicorn.run(
        "src.main:app",
        host=host,
        port=port,
        reload=reload_enabled,
        log_level=log_level,
        factory=False,
    )


if __name__ == "__main__":
    run()

