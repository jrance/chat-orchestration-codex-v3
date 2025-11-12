"""Tests for the graph_builder.compiler module."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from src.graph_builder.compiler import Compiler  # noqa: E402

EXAMPLES_DIR = ROOT_DIR / "schemas" / "examples"
EXAMPLE_FILES = sorted(EXAMPLES_DIR.glob("*.json"))
NON_INVOKABLE = {"m365_branches_v1.json", "xlsx_validation_v1.json"}


@pytest.mark.parametrize("example_path", EXAMPLE_FILES)
def test_compile_succeeds_for_all_examples(example_path: Path) -> None:
    payload = json.loads(example_path.read_text(encoding="utf-8"))

    compiler = Compiler()
    app = compiler.compile(payload)

    assert hasattr(app, "invoke")

    if example_path.name not in NON_INVOKABLE:
        entry_id = payload.get("entryId") or payload["nodes"][0]["id"]
        initial_state = {"_in": {entry_id: {"messages": [{"role": "user", "content": "hello"}]}}}

        final_state = app.invoke(initial_state)

        assert "nodes" in final_state
        assert entry_id in final_state["nodes"]


def test_router_example_routes_to_default_child() -> None:
    router_pkg = json.loads((EXAMPLES_DIR / "router_news_scores_v1.json").read_text(encoding="utf-8"))
    compiler = Compiler()
    app = compiler.compile(router_pkg)

    entry_id = router_pkg["entryId"]
    state = {"_in": {entry_id: {"messages": [{"role": "user", "content": "latest news?"}]}}}

    result = app.invoke(state)
    router_output = result["nodes"]["n_router"]["output"]

    assert router_output["__route__"] == "n_news"
    assert "n_scores" not in result["nodes"]
    assert "n_news" in result["nodes"]
