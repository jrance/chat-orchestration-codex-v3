from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_sequential(node_id: str, cfg: Dict[str, Any]):
    """
    Sequential is a structural node. The function here only records that the
    node executed. The graph edges drive actual ordering.
    """
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("_data", {})[node_id] = {"ok": True}
        push_trace(state, f"{node_id}: sequential checkpoint")
        return state
    return fn
