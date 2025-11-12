from __future__ import annotations
from typing import Any, Dict, List

def apply_edge_mappings(state: Dict[str, Any], edge: Dict[str, Any], src_node: str, dst_node: str) -> Dict[str, Any]:
    """
    Placeholder mapper:
      - Copies the *entire* source node's output into dst inputs under 'input'
      - Ignores transform/validators/reducers for now.
    Extend here with JSONPath, transforms, defaults, validators, reducers.
    """
    _data = state.get("_data", {})
    src_payload = _data.get(src_node, {})
    return {"input": src_payload}

def push_trace(state: Dict[str, Any], msg: str) -> None:
    state.setdefault("_trace", []).append(msg)
