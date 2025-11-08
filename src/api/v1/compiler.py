"""Compile endpoint wiring."""

from __future__ import annotations

from uuid import uuid4

from fastapi import APIRouter

from .models import CompileResponse, OrchestrationPackage

router = APIRouter()


@router.post("/compile", response_model=CompileResponse)
async def compile_orchestration(
    orchestration_package: OrchestrationPackage,
) -> CompileResponse:
    """Validate and (eventually) compile orchestration graphs."""
    graph_id = orchestration_package.meta_id or f"graph-{uuid4()}"
    return CompileResponse(ok=True, graph_id=graph_id, message="Compilation successful")

