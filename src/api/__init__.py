"""Top-level API router wiring."""

from fastapi import APIRouter

from .v1 import router as v1_router

api_router = APIRouter()
api_router.include_router(v1_router)

__all__ = ["api_router"]
