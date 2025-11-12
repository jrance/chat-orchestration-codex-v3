from __future__ import annotations
from typing import Any, Dict, List
from dataclasses import dataclass

from langgraph.graph import StateGraph, START, END  # pip install langgraph
from .types import Json, NodeSpec, Edge, CompileResult
from .mapping import apply_edge_mappings, push_trace
from .nodes import NODE_BUILDERS

@dataclass
class OrchestrationCompiler:
    """
    Minimal, readable compiler that:
      - Builds a LangGraph StateGraph of dict-state nodes
      - Adds a node for each IR node (stub functions)
      - Wires edges (simple unconditional; router uses conditional)
      - Leaves mapping, JSONPath, and real logic for a later PR
    """

    def compile(self, package: Json) -> CompileResult:
        # --- Normalize nodes/edges -------------------------------------------------
        nodes: Dict[str, NodeSpec] = {}
        for raw in package.get("nodes", []):
            nodes[raw["id"]] = NodeSpec(
                id=raw["id"],
                kind=raw["kind"],
                label=raw.get("label", raw["id"]),
                data=raw.get("data", {}),
                io=raw.get("io", {}),
            )

        edges: List[Edge] = []
        for raw in package.get("edges", []):
            edges.append({
                "id": raw.get("id", f"e:{raw['from']}→{raw['to']}"),
                "from_": raw["from"],
                "to": raw["to"],
                "label": raw.get("label", ""),
                "mapping": raw.get("mapping", []),
            })

        entry_id = package.get("entryId") or self._infer_entry(nodes, edges)

        # --- Build graph -----------------------------------------------------------
        graph = StateGraph(dict)
        built: Dict[str, Any] = {}

        for nid, spec in nodes.items():
            builder = NODE_BUILDERS.get(spec.kind)
            if not builder:
                # Unknown kind → no-op node with trace
                def _noop(state: Dict[str, Any], nid=nid) -> Dict[str, Any]:
                    push_trace(state, f"{nid}: [noop] unknown kind")
                    return state
                graph.add_node(nid, _noop)
                built[nid] = _noop
                continue

            fn = builder(nid, {"id": spec.id, "kind": spec.kind, "data": spec.data, "io": spec.io})
            graph.add_node(nid, fn)
            built[nid] = fn

        # START edge
        if entry_id and entry_id in nodes:
            graph.add_edge(START, entry_id)

        # Router conditional support (very light)
        router_nodes = [n for n in nodes.values() if n.kind == "router"]
        router_sources = set(n.id for n in router_nodes)

        # For unconditional edges, we do a simple add_edge.
        # For router sources, we still add plain edges so the graph is connected,
        # but route choice is simulated in the router node (we keep it simple here).
        for e in edges:
            src = e["from_"]; dst = e["to"]
            if src not in nodes or dst not in nodes:
                continue
            graph.add_edge(src, dst)

        # --- Compile to app --------------------------------------------------------
        app = graph.compile()

        # Attach a convenience runner that applies edge mappings naively.
        def run(initial_state: Dict[str, Any]) -> Dict[str, Any]:
            """
            Very simple runner:
              - Calls app.invoke() with dict state
              - Applies *placeholder* mapping before each node by listening to edges is out-of-scope here;
                for now, we stash source outputs in state['_inputs'][dst] when a node completes.
              - This keeps the scaffold tiny; mapping engine comes in a separate PR.
            """
            state = dict(initial_state or {})
            state.setdefault("_data", {})
            state.setdefault("_inputs", {})
            state.setdefault("_trace", [])

            # In this stub, we just run the compiled app once; LangGraph will walk edges.
            # If you want fine-grained control (per-edge mapping), add a custom checkpointer
            # and update state['_inputs'] inside node fns or via middleware hooks.
            return app.invoke(state)

        # Return objects so tests can call result["app"].invoke({...})
        out: CompileResult = {
            "app": app,
            "graph": graph,
            "nodes": nodes,
            "edges": edges,
        }
        return out

    # --- helpers ------------------------------------------------------------------

    def _infer_entry(self, nodes: Dict[str, NodeSpec], edges: List[Edge]) -> str | None:
        """
        If entryId not set, pick a node with no incoming edges.
        """
        incoming: Dict[str, int] = {nid: 0 for nid in nodes}
        for e in edges:
            if e["to"] in incoming:
                incoming[e["to"]] += 1
        candidates = [nid for nid, count in incoming.items() if count == 0]
        return candidates[0] if candidates else None
