from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_parallel(node_id: str, cfg: Dict[str, Any]):
    """
    Stub parallel node:
      - items mode: records that it would fan out over N items.
      - branches mode: records broadcast fields and child list.
    Real impl: use LangGraph 'Send' for bounded concurrency.
    """
    data = cfg.get("data", {})
    mode = data.get("mode", "items")

    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("_data", {})[node_id] = {"mode": mode, "observed": True}
        push_trace(state, f"{node_id}: parallel ({mode}) checkpoint")
        return state
    return fn
