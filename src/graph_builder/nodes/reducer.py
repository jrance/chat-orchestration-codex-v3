from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_reducer(node_id: str, cfg: Dict[str, Any]):
    """
    Stub reducer: marks that it would aggregate child results.
    Real impl: perform configured strategies (list_concat, sum, etc.).
    """
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("_data", {})[node_id] = {"reduced": True}
        push_trace(state, f"{node_id}: reduced")
        return state
    return fn
