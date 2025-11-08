"""Shared FastAPI middleware wiring."""

from __future__ import annotations

import os
from time import perf_counter
from typing import Iterable, Sequence

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.responses import Response


def _parse_csv(value: str, *, fallback: Sequence[str]) -> Sequence[str]:
    parts = [item.strip() for item in value.split(",")]
    return [item for item in parts if item] or list(fallback)


class ProcessTimeMiddleware(BaseHTTPMiddleware):
    """Annotate responses with processing time to aid debugging."""

    def __init__(self, app: FastAPI, header_name: str = "X-Process-Time-Ms") -> None:  # type: ignore[override]
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        start = perf_counter()
        response = await call_next(request)
        response.headers[self.header_name] = f"{(perf_counter() - start) * 1000:.2f}"
        return response


def configure_core_middleware(app: FastAPI) -> None:
    """Attach the default middleware stack."""

    cors_origins_env = os.getenv("CORS_ALLOW_ORIGINS", "*")
    allowed_hosts_env = os.getenv("ALLOWED_HOSTS", "*")

    allow_origins: Iterable[str]
    allow_hosts: Sequence[str]

    if cors_origins_env == "*":
        allow_origins = ["*"]
    else:
        allow_origins = _parse_csv(cors_origins_env, fallback=["*"])

    allow_hosts = (
        ["*"]
        if allowed_hosts_env == "*"
        else list(_parse_csv(allowed_hosts_env, fallback=["*"]))
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(allow_origins),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allow_hosts)
    app.add_middleware(ProcessTimeMiddleware)

