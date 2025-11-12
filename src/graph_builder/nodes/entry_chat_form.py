from __future__ import annotations
from typing import Any, Dict
from ..mapping import push_trace

def build_entry_chat_form(node_id: str, cfg: Dict[str, Any]):
    """
    Stashes the entry payload (messages+form) into _data[node_id].
    Real impl: validate against formSchemaRef, normalize files → FileRef(s).
    """
    def fn(state: Dict[str, Any]) -> Dict[str, Any]:
        payload = {
            "messages": state.get("messages", []),
            "form": state.get("form", {"values": {}}),
            "files": state.get("files", []),
        }
        state.setdefault("_data", {})[node_id] = payload
        push_trace(state, f"{node_id}: entry captured (messages={len(payload['messages'])})")
        return state
    return fn
