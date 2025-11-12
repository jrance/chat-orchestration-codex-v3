from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, TypedDict

# Basic shape of orchestration IR (v1.2 core)
Json = Dict[str, Any]
NodeFn = Callable[[Dict[str, Any]], Dict[str, Any]]

class Edge(TypedDict, total=False):
    id: str
    from_: str  # mapped from "from"
    to: str
    label: str
    mapping: List[Dict[str, Any]]

@dataclass
class NodeSpec:
    id: str
    kind: str
    label: str
    data: Dict[str, Any]
    io: Dict[str, Any]

class CompileResult(TypedDict):
    "What the compiler returns."
    app: Any              # langgraph compiled app
    graph: Any            # optional: raw graph before compile()
    nodes: Dict[str, NodeSpec]
    edges: List[Edge]
