from __future__ import annotations

import asyncio
import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple
from collections import deque

from langchain_core.runnables.config import RunnableConfig
from langchain_core.orchestration.state import OrchestrationState

# Prefer langgraph.types for Send/Command when available
try:
    from langgraph.types import Send, Command  # type: ignore
except Exception:  # pragma: no cover
    from langchain_core.commands import Command  # type: ignore
    from langgraph.types import Send  # type: ignore


# -----------------------------
# Helpers: path get/set + reducers
# -----------------------------

def _get_by_path(obj: Dict[str, Any], path: str) -> Any:
    """Supports simple dotted paths like 'results' or 'variables.objects'."""
    if not path or path == "$":
        return obj
    cur: Any = obj
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_by_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    """Supports simple dotted paths like 'variables.objects'."""
    if not path or path == "$":
        raise ValueError("Refusing to overwrite root via path '$' in _set_by_path")
    parts = path.split(".")
    cur: Dict[str, Any] = obj
    for p in parts[:-1]:
        nxt = cur.get(p)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[p] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Deep merge dict b into a, returning a new dict."""
    out = copy.deepcopy(a)
    for k, v in b.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _apply_reduce(strategy: str, acc: Any, val: Any) -> Any:
    if strategy == "last_write_wins":
        return val
    if strategy == "list_concat":
        if acc is None:
            acc = []
        if val is None:
            return acc
        if not isinstance(acc, list):
            raise TypeError(f"Accumulator is not list for list_concat: {type(acc)}")
        if isinstance(val, list):
            return acc + val
        return acc + [val]
    if strategy == "dict_merge_shallow":
        if acc is None:
            acc = {}
        if val is None:
            return acc
        if not isinstance(acc, dict) or not isinstance(val, dict):
            raise TypeError("dict_merge_shallow requires dict inputs")
        merged = dict(acc)
        merged.update(val)
        return merged
    if strategy == "dict_merge_deep":
        if acc is None:
            acc = {}
        if val is None:
            return acc
        if not isinstance(acc, dict) or not isinstance(val, dict):
            raise TypeError("dict_merge_deep requires dict inputs")
        return _deep_merge(acc, val)
    if strategy == "sum":
        return (acc or 0) + (val or 0)
    if strategy == "min":
        return val if acc is None else min(acc, val)
    if strategy == "max":
        return val if acc is None else max(acc, val)
    if strategy == "avg":
        # store as (sum, count) during accumulation, convert at finalize
        if acc is None:
            acc = (0.0, 0)
        s, c = acc
        if val is None:
            return (s, c)
        return (s + float(val), c + 1)
    raise ValueError(f"Unknown reducer strategy: {strategy}")


def _finalize_avg(acc: Any) -> Any:
    if acc is None:
        return None
    s, c = acc
    return (s / c) if c else None


# -----------------------------
# Join manager (supports bounded concurrency + nesting)
# -----------------------------

@dataclass
class JoinGroup:
    group_id: str
    mode: str  # "items" or "branches"
    created_at: float
    timeout_sec: float
    on_timeout: str  # "partial" or "fail"
    wait_for: str  # "all" or "firstN" or "firstBest" (firstBest treated as firstN here)
    first_n: Optional[int]

    # fanout plan
    target_node: Optional[str] = None  # where to Send tasks
    pending: Deque[Any] = field(default_factory=deque)
    inflight: int = 0
    completed: int = 0
    total: int = 0
    concurrency: int = 8

    # reduction config
    reduce_fields: List[Dict[str, str]] = field(default_factory=list)  # [{target,strategy}]
    acc: Dict[str, Any] = field(default_factory=dict)

    # finalize routing
    next_node: Optional[str] = None

    # base parent snapshot (so reducer can merge into parent once)
    parent_snapshot: Dict[str, Any] = field(default_factory=dict)

    done: bool = False


class InMemoryJoinManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._groups: Dict[Tuple[str, str], JoinGroup] = {}  # (thread_key, group_id) -> JoinGroup

    async def create_group(self, thread_key: str, group: JoinGroup) -> None:
        async with self._lock:
            self._groups[(thread_key, group.group_id)] = group

    async def get_group(self, thread_key: str, group_id: str) -> JoinGroup:
        async with self._lock:
            g = self._groups.get((thread_key, group_id))
            if not g:
                raise KeyError(f"JoinGroup not found: {thread_key=} {group_id=}")
            return g

    async def delete_group(self, thread_key: str, group_id: str) -> None:
        async with self._lock:
            self._groups.pop((thread_key, group_id), None)


_JOIN = InMemoryJoinManager()


def _thread_key(config: RunnableConfig) -> str:
    # Prefer your configured thread_id if present
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    tid = cfg.get("thread_id") or cfg.get("conversation_id") or cfg.get("run_id")
    return str(tid) if tid else "default-thread"


# -----------------------------
# Parallel / Reducer execute functions (your requested shape)
# -----------------------------

_FANOUT_CTX_KEY = "__fanout_ctx__"  # stored in state.variables for nested reducer correlation


async def parallel_execute(state: OrchestrationState, config: RunnableConfig, *, node_id: str, cfg: Dict[str, Any]) -> Any:
    """
    Parallel node:
      - items mode: bounded fanout over an array
      - branches mode: bounded fanout over child nodes
    Uses Command(goto=[Send(...), ...]) and does NOT rely on static edges.
    """
    data = (cfg or {}).get("data", {})
    mode = data.get("mode", "items")
    concurrency = int(data.get("concurrency", 8))
    timeout_sec = float(data.get("timeoutSec", 60))

    join_cfg = data.get("join", {}) or {}
    wait_for = join_cfg.get("waitFor", "all")
    on_timeout = join_cfg.get("onTimeout", "partial")
    first_n = int(join_cfg.get("n")) if join_cfg.get("n") is not None else None
    if wait_for == "firstBest":
        # Treat as firstN for now unless you have a scoring contract
        wait_for = "firstN"
        first_n = first_n or 1

    # Compiler should provide:
    #   - items: { itemPath: "variables.catalog_objects" } OR you pass items directly in state under "items"
    #   - target: first node in the per-item subgraph (items mode)
    #   - branches.children: list of child node ids (branches mode)
    #   - reducer_id + next_node (where reducer should route after finalize)
    target_node = data.get("target")  # recommended compiler-provided field
    reducer_id = data.get("reducer")  # recommended compiler-provided field
    next_node = data.get("next")      # optional "after reducer" node

    if not reducer_id:
        raise ValueError(f"Parallel node {node_id} missing data.reducer (reducer node id)")

    group_id = f"{node_id}:{uuid.uuid4().hex}"
    tkey = _thread_key(config)

    parent_snapshot = copy.deepcopy(state)
    # row-level fields must not leak from parent into each item unless you want that behavior
    if isinstance(parent_snapshot, dict):
        parent_snapshot["row"] = {}

    group = JoinGroup(
        group_id=group_id,
        mode=mode,
        created_at=time.time(),
        timeout_sec=timeout_sec,
        on_timeout=on_timeout,
        wait_for=wait_for,
        first_n=first_n,
        concurrency=concurrency,
        target_node=target_node,
        next_node=next_node,
        parent_snapshot=parent_snapshot,
    )

    sends: List[Send] = []

    if mode == "items":
        # 1) Resolve items list
        items_path = ((data.get("items") or {}).get("itemPath")) or ""
        items = None

        # Common pattern: compiler maps array directly to state["items"] (see your xlsx sample)
        if isinstance(state, dict) and "items" in state:
            items = state.get("items")

        # Fallback: dotted path within state
        if items is None and items_path:
            # IR uses JSONPath-like "$.rows" (schema) :contentReference[oaicite:2]{index=2}
            # We implement a minimal "$.a.b" -> "a.b"
            dotted = items_path.strip()
            if dotted.startswith("$."):
                dotted = dotted[2:]
            elif dotted == "$":
                dotted = ""
            items = _get_by_path(state, dotted) if dotted else state

        if not isinstance(items, list):
            raise ValueError(f"Parallel(items) expects a list at 'items' or {items_path!r}")

        if not group.target_node:
            raise ValueError(f"Parallel(items) node {node_id} missing data.target (first child node id)")

        group.total = len(items)
        group.pending = deque(items)

        # 2) Register group
        await _JOIN.create_group(tkey, group)

        # 3) Dispatch initial window
        while group.inflight < group.concurrency and group.pending:
            item = group.pending.popleft()
            group.inflight += 1

            child_state = copy.deepcopy(parent_snapshot)
            # carry correlation context in variables so reducer can identify group
            child_vars = dict(child_state.get("variables", {}) or {})
            child_vars[_FANOUT_CTX_KEY] = {
                "group_id": group_id,
                "reducer_id": reducer_id,
                "parallel_id": node_id,
                "next_node": next_node,
                "mode": "items",
            }
            child_state["variables"] = child_vars
            child_state["row"] = item  # row-level state starts as the current item

            sends.append(Send(group.target_node, child_state))

        # Route to first batch of tasks
        return Command(goto=sends)

    if mode == "branches":
        branches = (data.get("branches") or {})
        children: List[str] = list(branches.get("children") or [])
        if not children:
            raise ValueError(f"Parallel(branches) node {node_id} missing branches.children")

        # In branches mode, each branch is a Send to that child
        group.total = len(children)
        group.pending = deque(children)
        group.target_node = None  # each pending item is already a node id

        await _JOIN.create_group(tkey, group)

        while group.inflight < group.concurrency and group.pending:
            child_id = group.pending.popleft()
            group.inflight += 1

            child_state = copy.deepcopy(parent_snapshot)
            child_vars = dict(child_state.get("variables", {}) or {})
            child_vars[_FANOUT_CTX_KEY] = {
                "group_id": group_id,
                "reducer_id": reducer_id,
                "parallel_id": node_id,
                "next_node": next_node,
                "mode": "branches",
            }
            child_state["variables"] = child_vars
            sends.append(Send(child_id, child_state))

        return Command(goto=sends)

    raise ValueError(f"Unknown parallel mode: {mode}")


def build_parallel(node_id: str, cfg: Dict[str, Any]) -> Any:
    data = cfg.get("data", {})
    # wrapper matches your requested signature
    async def wrapped(state: OrchestrationState, config: RunnableConfig) -> Any:
        return await parallel_execute(state, config, node_id=node_id, cfg=cfg)
    return wrapped


async def reducer_execute(state: OrchestrationState, config: RunnableConfig, *, node_id: str, cfg: Dict[str, Any]) -> Any:
    """
    Reducer node:
      - called once per completed child branch (items or branches)
      - accumulates according to cfg.data.fields strategies :contentReference[oaicite:3]{index=3}
      - pumps the next batch to maintain bounded concurrency
      - finalizes exactly once (merges into parent state) and routes to cfg.data.next (if provided)
    """
    data = (cfg or {}).get("data", {})
    fields: List[Dict[str, str]] = list(data.get("fields") or [])
    # "next" can be specified on reducer node itself (recommended)
    next_node = data.get("next")

    vars_ = (state.get("variables") or {}) if isinstance(state, dict) else {}
    ctx = vars_.get(_FANOUT_CTX_KEY) if isinstance(vars_, dict) else None
    if not isinstance(ctx, dict):
        raise ValueError(f"Reducer {node_id} missing variables.{_FANOUT_CTX_KEY} fanout context")

    group_id = ctx.get("group_id")
    reducer_id = ctx.get("reducer_id")
    mode = ctx.get("mode")
    parallel_id = ctx.get("parallel_id")
    ctx_next = ctx.get("next_node")

    if reducer_id and reducer_id != node_id:
        # Safety: avoid mis-wiring reducer contexts
        raise ValueError(f"Reducer context mismatch: ctx.reducer_id={reducer_id} but current node is {node_id}")

    tkey = _thread_key(config)
    group = await _JOIN.get_group(tkey, group_id)

    # Initialize reduction contract once
    if not group.reduce_fields:
        group.reduce_fields = fields
        for f in fields:
            group.acc.setdefault(f["target"], None)

    # Timeout handling
    if group.timeout_sec and (time.time() - group.created_at) > group.timeout_sec:
        if group.on_timeout == "fail":
            await _JOIN.delete_group(tkey, group_id)
            raise TimeoutError(f"Reducer timeout in group {group_id} (parallel={parallel_id})")
        # partial finalize: mark done and proceed with what we have
        group.done = True

    # 1) Accumulate this child's contributions
    for f in group.reduce_fields:
        target = f["target"]
        strat = f["strategy"]

        val = _get_by_path(state, target)  # supports dotted targets like "variables.results"
        group.acc[target] = _apply_reduce(strat, group.acc.get(target), val)

    # 2) Mark completion of this child
    group.inflight = max(0, group.inflight - 1)
    group.completed += 1

    # Determine completion threshold
    target_done = group.total
    if group.wait_for == "firstN" and group.first_n:
        target_done = min(group.total, group.first_n)

    # If not done: pump more work (bounded concurrency window)
    sends: List[Send] = []
    if not group.done and group.completed < target_done:
        if group.mode == "items":
            # pending items are raw items; send to group.target_node
            while group.inflight < group.concurrency and group.pending:
                item = group.pending.popleft()
                group.inflight += 1

                child_state = copy.deepcopy(group.parent_snapshot)
                child_vars = dict(child_state.get("variables", {}) or {})
                child_vars[_FANOUT_CTX_KEY] = {
                    "group_id": group.group_id,
                    "reducer_id": node_id,
                    "parallel_id": parallel_id,
                    "next_node": ctx_next,
                    "mode": "items",
                }
                child_state["variables"] = child_vars
                child_state["row"] = item
                sends.append(Send(group.target_node, child_state))

        elif group.mode == "branches":
            while group.inflight < group.concurrency and group.pending:
                child_id = group.pending.popleft()
                group.inflight += 1

                child_state = copy.deepcopy(group.parent_snapshot)
                child_vars = dict(child_state.get("variables", {}) or {})
                child_vars[_FANOUT_CTX_KEY] = {
                    "group_id": group.group_id,
                    "reducer_id": node_id,
                    "parallel_id": parallel_id,
                    "next_node": ctx_next,
                    "mode": "branches",
                }
                child_state["variables"] = child_vars
                sends.append(Send(child_id, child_state))

        # Important: reducer does NOT advance the main graph yet; it only schedules more tasks.
        if sends:
            return Command(goto=sends)

        # Otherwise: nothing to do until more completions arrive
        return Command()

    # 3) Finalize (exactly once)
    if group.done or group.completed >= target_done:
        if group.done:
            # already finalized by timeout path
            pass
        group.done = True

        # Convert avg accumulators if any
        final_updates: Dict[str, Any] = {}
        for f in group.reduce_fields:
            target = f["target"]
            strat = f["strategy"]
            val = group.acc.get(target)
            if strat == "avg":
                val = _finalize_avg(val)
            _set_by_path(final_updates, target, val)

        # Merge into parent snapshot as the base
        merged_parent = copy.deepcopy(group.parent_snapshot)
        merged_parent = _deep_merge(merged_parent, final_updates)

        # Clear fanout ctx in finalized state (optional)
        if isinstance(merged_parent.get("variables"), dict):
            merged_parent["variables"].pop(_FANOUT_CTX_KEY, None)

        await _JOIN.delete_group(tkey, group_id)

        # Route onward: reducer node can specify next, otherwise use ctx.next_node, otherwise stop.
        goto = next_node or ctx_next or ()
        return Command(update=merged_parent, goto=goto)

    return Command()


def build_reducer(node_id: str, cfg: Dict[str, Any]) -> Any:
    async def wrapped(state: OrchestrationState, config: RunnableConfig) -> Any:
        return await reducer_execute(state, config, node_id=node_id, cfg=cfg)
    return wrapped
