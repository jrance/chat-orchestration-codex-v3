from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_agent_codeless(node_id: str, cfg: Dict[str, Any]):
    """
    Stub LLM agent:
      - records system instructions and attached tools
      - returns a placeholder structured output
    Real impl: apply historyPolicy, call LLM, honor structuredOutput, tool use, etc.
    """
    data = cfg.get("data", {})
    sys = (data.get("systemInstructions") or "").splitlines()[0:1]
    tools = data.get("tools", {}).get("attached", [])
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        output = {"text": f"[{node_id}] placeholder answer", "tools_used": tools}
        state.setdefault("_data", {})[node_id] = output
        push_trace(state, f"{node_id}: agent executed (tools={len(tools)})")
        return state
    return fn
