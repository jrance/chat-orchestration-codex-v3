from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_publisher(node_id: str, cfg: Dict[str, Any]):
    """
    Stub publisher: simulates a side-effect and returns a URL placeholder.
    Real impl: perform SharePoint/webhook/email/etc.
    """
    sink = cfg.get("data", {}).get("sink", "sink")
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        url = f"https://example.com/{node_id}/artifact"
        state.setdefault("_data", {})[node_id] = {"sink": sink, "url": url}
        push_trace(state, f"{node_id}: published to {sink} → {url}")
        return state
    return fn
