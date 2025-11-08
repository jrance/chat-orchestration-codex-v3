"""Tests for the /v1/compile endpoint."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from main import app

client = TestClient(app)

_EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "schemas" / "examples"
_DEFAULT_EXAMPLE = "router_news_scores_v1.json"


def _load_example(name: str) -> dict:
    with (_EXAMPLES_DIR / name).open("r", encoding="utf-8") as example_file:
        return json.load(example_file)


def test_compile_accepts_valid_package() -> None:
    payload = _load_example(_DEFAULT_EXAMPLE)

    response = client.post("/v1/compile", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["graph_id"] == payload["meta"]["id"]
    assert body["message"] == "Compilation successful"


def test_compile_rejects_invalid_package() -> None:
    invalid_payload = {"meta": {"id": "demo", "name": "Demo", "version": "1"}}

    response = client.post("/v1/compile", json=invalid_payload)

    assert response.status_code == 422
