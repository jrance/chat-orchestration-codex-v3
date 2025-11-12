from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_mcp_server(node_id: str, cfg: Dict[str, Any]):
    """
    Stub MCP server node: registers endpoint metadata.
    Real impl: establish MCP client session (tools/resources/prompts).
    """
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        state.setdefault("_data", {})[node_id] = {"mcp": "registered"}
        push_trace(state, f"{node_id}: mcp server registered")
        return state
    return fn
