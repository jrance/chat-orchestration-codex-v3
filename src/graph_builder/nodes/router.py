from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_router(node_id: str, cfg: Dict[str, Any]):
    """
    Stub router: chooses the first configured target unless state['_route'][node_id] is set.
    Real impl: LLM or deterministic routing returning one of the target node ids.
    """
    targets = cfg.get("data", {}).get("targets", [])
    default_target = targets[0] if targets else None

    def route_fn(state: Dict[str, Any]) -> Dict[str, Any]:
        forced = state.get("_route", {}).get(node_id)
        choice = forced or default_target
        state.setdefault("_data", {})[node_id] = {"target": choice}
        push_trace(state, f"{node_id}: routed → {choice}")
        # Store next node choice so the compiler can wire conditional edges.
        state.setdefault("_next", {})[node_id] = choice
        return state

    return route_fn
