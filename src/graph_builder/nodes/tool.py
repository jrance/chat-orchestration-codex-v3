from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_tool(node_id: str, cfg: Dict[str, Any]):
    """
    Stub tool node:
      - records toolId and echoes 'input' from mapping layer
    Real impl: resolve argPolicies (LLMHidden), call transport (http/native/mcp), enforce privacy/telemetry.
    """
    tool_id = cfg.get("data", {}).get("toolId", "tool.unknown")
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        # If mapping stage populated state['_inputs'][node_id], echo it back.
        inputs = state.get("_inputs", {}).get(node_id, {})
        out = {"toolId": tool_id, "echo": inputs}
        state.setdefault("_data", {})[node_id] = out
        push_trace(state, f"{node_id}: tool('{tool_id}') executed")
        return state
    return fn
