from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_output(node_id: str, cfg: Dict[str, Any]):
    """
    Output finalizer: validates/normalizes final payload (stubbed).
    Real impl: validate against schemaRef and set state['_final'].
    """
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        # For now, pass through latest data snapshot:
        final_payload = {"ok": True, "summary": "stub"}
        state["_final"] = final_payload
        state.setdefault("_data", {})[node_id] = final_payload
        push_trace(state, f"{node_id}: finalized output")
        return state
    return fn
