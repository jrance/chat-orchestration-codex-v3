"""Version 1 API surface."""

from fastapi import APIRouter

from .compiler import router as compiler_router

router = APIRouter(prefix="/v1")
router.include_router(compiler_router, tags=["compile"])

__all__ = ["router"]
