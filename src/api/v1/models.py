"""Request and response models for the v1 compile endpoint."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from jsonschema import Draft202012Validator
from pydantic import BaseModel, RootModel, model_validator

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SCHEMA_PATH = _REPO_ROOT / "schemas" / "orchestration_ir.schema.json"


@lru_cache(maxsize=1)
def _load_ir_schema() -> Dict[str, Any]:
    with _SCHEMA_PATH.open("r", encoding="utf-8") as schema_file:
        return json.load(schema_file)


@lru_cache(maxsize=1)
def _get_ir_validator() -> Draft202012Validator:
    return Draft202012Validator(schema=_load_ir_schema())


class OrchestrationPackage(RootModel[Dict[str, Any]]):
    """Wrapper around the orchestration IR that enforces the JSON schema."""

    @model_validator(mode="after")
    def validate_schema(self) -> "OrchestrationPackage":
        validator = _get_ir_validator()
        errors = sorted(validator.iter_errors(self.root), key=lambda err: err.json_path)
        if errors:
            formatted = [
                f"{err.json_path or '$'}: {err.message}"
                for err in errors
            ]
            raise ValueError("; ".join(formatted))
        return self

    @property
    def meta_id(self) -> Optional[str]:
        meta = self.root.get("meta") if isinstance(self.root, dict) else None
        if isinstance(meta, dict):
            meta_id = meta.get("id")
            if isinstance(meta_id, str) and meta_id.strip():
                return meta_id
        return None


class CompileResponse(BaseModel):
    """Response envelope for compilation requests."""

    ok: bool = False
    graph_id: Optional[str] = None
    message: str = "Not implemented"
