# parallel_reducer.py
from __future__ import annotations

import asyncio
import copy
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Literal, Optional, Tuple

from langchain_core.runnables.config import RunnableConfig
from langchain_core.orchestration.state import OrchestrationState

try:
    # LangGraph 1.x
    from langgraph.types import Send, Command  # type: ignore
except Exception:  # pragma: no cover
    # Fallback for some environments
    from langchain_core.commands import Command  # type: ignore
    from langgraph.types import Send  # type: ignore


# -----------------------------------------------------------------------------
# IMPORTANT RULE (fixes your 'row' InvalidUpdateError)
# -----------------------------------------------------------------------------
# In a parallel fan-out, DO NOT emit 'row' in Command(update=...) from child nodes.
# Multiple branches can complete in the same superstep, and 'row' is a single-value
# channel (LastValue). If two branches update 'row' in the same step, LangGraph
# raises: InvalidUpdateError: At key 'row': Can receive only one value per step.
#
# This file guarantees:
#   - parallel node NEVER updates 'row'
#   - reducer node NEVER updates 'row'
#   - reducer also rejects configs that attempt to reduce into 'row' or 'row.*'
#
# You must ALSO ensure your tool/agent nodes inside the fan-out do NOT return a full
# state dict (which implicitly updates row). They should return Command(update={...})
# containing only keys they actually change, and NEVER include 'row'.
# -----------------------------------------------------------------------------


# Supports nested parallel/reducer safely by using a stack in variables.
FANOUT_STACK_KEY = "__fanout_stack__"


# -----------------------------------------------------------------------------
# Small helpers
# -----------------------------------------------------------------------------

def _thread_key(config: RunnableConfig) -> str:
    cfg = (config or {}).get("configurable", {}) if isinstance(config, dict) else {}
    tid = cfg.get("thread_id") or cfg.get("conversation_id") or cfg.get("run_id")
    return str(tid) if tid else "default-thread"


def _get_path(state: Dict[str, Any], path: str) -> Any:
    """
    Minimal JSONPath-ish support used by ParallelNode itemPath:
      - "$.a.b.c" or "a.b.c"
    """
    if not path:
        return None
    p = path.strip()
    if p.startswith("$."):
        p = p[2:]
    if p == "$":
        return state
    cur: Any = state
    for part in p.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_path(obj: Dict[str, Any], path: str, value: Any) -> None:
    """
    Dot-path setter:
      - "results" or "variables.object_results"
    """
    if not path:
        raise ValueError("path must be non-empty")
    if path == "$":
        raise ValueError("Refusing to overwrite root via '$'")
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
        base = [] if acc is None else acc
        if val is None:
            return base
        if not isinstance(base, list):
            raise TypeError("list_concat accumulator must be a list")
        return base + (val if isinstance(val, list) else [val])
    if strategy == "dict_merge_shallow":
        base = {} if acc is None else acc
        if val is None:
            return base
        if not isinstance(base, dict) or not isinstance(val, dict):
            raise TypeError("dict_merge_shallow requires dict values")
        merged = dict(base)
        merged.update(val)
        return merged
    if strategy == "dict_merge_deep":
        base = {} if acc is None else acc
        if val is None:
            return base
        if not isinstance(base, dict) or not isinstance(val, dict):
            raise TypeError("dict_merge_deep requires dict values")
        return _deep_merge(base, val)
    if strategy == "sum":
        return (acc or 0) + (val or 0)
    if strategy == "min":
        return val if acc is None else min(acc, val)
    if strategy == "max":
        return val if acc is None else max(acc, val)
    if strategy == "avg":
        # store (sum, count) until finalize
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


def _stack_get(vars_: Dict[str, Any]) -> List[Dict[str, Any]]:
    st = vars_.get(FANOUT_STACK_KEY)
    if st is None:
        st = []
        vars_[FANOUT_STACK_KEY] = st
    if not isinstance(st, list):
        raise ValueError(f"variables.{FANOUT_STACK_KEY} must be a list")
    return st


# -----------------------------------------------------------------------------
# Join manager (in-memory; swap with Redis later if needed)
# -----------------------------------------------------------------------------

@dataclass
class JoinGroup:
    group_id: str
    mode: Literal["items", "branches"]
    created_at: float
    timeout_sec: float
    on_timeout: Literal["partial", "fail"]
    wait_for: Literal["all", "firstN", "firstBest"]
    first_n: Optional[int]

    # fan-out internals
    concurrency: int
    pending: Deque[Any] = field(default_factory=deque)
    inflight: int = 0
    completed: int = 0
    total: int = 0

    # for "items" we need to know where to pump the next work
    items_target_node: Optional[str] = None

    # reduction
    reduce_fields: List[Dict[str, str]] = field(default_factory=list)  # [{target, strategy}]
    acc: Dict[str, Any] = field(default_factory=dict)

    # routing after completion
    next_node: Optional[str] = None
    done: bool = False


class InMemoryJoinManager:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._groups: Dict[Tuple[str, str], JoinGroup] = {}

    async def put(self, tkey: str, group: JoinGroup) -> None:
        async with self._lock:
            self._groups[(tkey, group.group_id)] = group

    async def get(self, tkey: str, gid: str) -> JoinGroup:
        async with self._lock:
            g = self._groups.get((tkey, gid))
            if not g:
                raise KeyError(f"JoinGroup not found: {tkey=} {gid=}")
            return g

    async def delete(self, tkey: str, gid: str) -> None:
        async with self._lock:
            self._groups.pop((tkey, gid), None)


JOIN = InMemoryJoinManager()


# -----------------------------------------------------------------------------
# PARALLEL
# -----------------------------------------------------------------------------

def _resolve_items_target(node_id: str, cfg: Dict[str, Any]) -> str:
    """
    Your IR schema doesn't include "target", so your compiler must inject it.
    Supported injection locations (first found wins):
      - cfg["items_target"]
      - cfg["child_id"]
      - cfg["children"][0]
      - cfg["data"]["items"]["target"]
    """
    data = (cfg or {}).get("data", {}) or {}
    items = (data.get("items") or {}) if isinstance(data.get("items"), dict) else {}

    for key in ("items_target", "child_id"):
        v = cfg.get(key)
        if isinstance(v, str) and v:
            return v

    children = cfg.get("children")
    if isinstance(children, list) and children and isinstance(children[0], str) and children[0]:
        return children[0]

    v = items.get("target")
    if isinstance(v, str) and v:
        return v

    raise ValueError(
        f"Parallel(items) node '{node_id}' needs a target child id injected into cfg "
        "(cfg.items_target or cfg.child_id or cfg.children[0] or data.items.target)."
    )


async def parallel_execute(state: OrchestrationState, config: RunnableConfig, *, node_id: str, cfg: Dict[str, Any]) -> Any:
    data = (cfg or {}).get("data", {}) or {}

    mode: Literal["items", "branches"] = data.get("mode", "items")
    concurrency = int(data.get("concurrency", 8))
    timeout_sec = float(data.get("timeoutSec", 60))

    join_cfg = data.get("join") or {}
    wait_for: Literal["all", "firstN", "firstBest"] = join_cfg.get("waitFor", "all")
    on_timeout: Literal["partial", "fail"] = join_cfg.get("onTimeout", "partial")
    first_n = int(join_cfg.get("n")) if wait_for == "firstN" and join_cfg.get("n") is not None else None
    if wait_for == "firstBest":
        # minimal behavior: "first best" behaves like "first 1" at the join layer;
        # scoring/selection should be handled by your agents/tools.
        first_n = 1

    # Determine items/children
    sends: List[Send] = []

    parent_messages = state.get("messages", [])
    parent_form = state.get("form", {})
    parent_vars = state.get("variables", {}) or {}
    if not isinstance(parent_vars, dict):
        raise ValueError("state.variables must be a dict")

    # We'll reuse variables dict structure across child states, but each child gets its own copy.
    base_vars = dict(parent_vars)

    group_id = f"{node_id}:{uuid.uuid4().hex}"
    next_node = (data.get("next") or cfg.get("next_node") or cfg.get("next"))  # compiler can inject this
    items_target_node: Optional[str] = None

    if mode == "items":
        items_path = None
        items_cfg = data.get("items") or {}
        if isinstance(items_cfg, dict):
            items_path = items_cfg.get("itemPath")
        if not items_path:
            # compiler can inject a concrete items array directly at cfg["items"]
            items = cfg.get("items")
        else:
            items = _get_path(state, items_path)

        if not isinstance(items, list):
            raise ValueError(f"Parallel(items) expected list at itemPath={items_path!r} (or cfg['items'])")

        items_target_node = _resolve_items_target(node_id, cfg)

        group = JoinGroup(
            group_id=group_id,
            mode="items",
            created_at=time.time(),
            timeout_sec=timeout_sec,
            on_timeout=on_timeout,
            wait_for=wait_for,
            first_n=first_n,
            concurrency=max(1, concurrency),
            total=len(items),
            items_target_node=items_target_node,
            next_node=next_node if isinstance(next_node, str) else None,
        )

        # queue all items; pump initial window
        group.pending = deque(items)
        initial = min(group.concurrency, group.total)
        for _ in range(initial):
            item = group.pending.popleft()
            group.inflight += 1

            child_vars = dict(base_vars)
            stack = _stack_get(child_vars)
            stack.append(
                {
                    "parallel_node_id": node_id,
                    "group_id": group_id,
                    "mode": "items",
                    "next_node": group.next_node,
                }
            )

            child_state: Dict[str, Any] = {
                "messages": parent_messages,
                "form": parent_form,
                "variables": child_vars,
                "row": item,  # branch-local; DO NOT merge this back into parent
            }
            sends.append(Send(items_target_node, child_state))

        await JOIN.put(_thread_key(config), group)
        return Command(goto=sends)

    if mode == "branches":
        branches_cfg = data.get("branches") or {}
        if not isinstance(branches_cfg, dict):
            branches_cfg = {}
        children = branches_cfg.get("children") or []
        if not isinstance(children, list) or not all(isinstance(x, str) for x in children):
            raise ValueError("Parallel(branches) requires data.branches.children: string[]")

        broadcast = branches_cfg.get("broadcast") or ["messages", "form", "variables"]
        if not isinstance(broadcast, list):
            broadcast = ["messages", "form", "variables"]

        group = JoinGroup(
            group_id=group_id,
            mode="branches",
            created_at=time.time(),
            timeout_sec=timeout_sec,
            on_timeout=on_timeout,
            wait_for=wait_for,
            first_n=first_n,
            concurrency=max(1, concurrency),
            total=len(children),
            items_target_node=None,
            next_node=next_node if isinstance(next_node, str) else None,
        )

        # In branches mode we fire them all immediately (bounded by concurrency only if you want;
        # for simplicity we dispatch all and let your overall graph load manage, or you can pump).
        # We'll still honor "concurrency" by pumping.
        group.pending = deque(children)
        initial = min(group.concurrency, group.total)
        for _ in range(initial):
            child_id = group.pending.popleft()
            group.inflight += 1

            child_vars = dict(base_vars)
            stack = _stack_get(child_vars)
            stack.append(
                {
                    "parallel_node_id": node_id,
                    "group_id": group_id,
                    "mode": "branches",
                    "next_node": group.next_node,
                }
            )

            child_state: Dict[str, Any] = {"variables": child_vars}
            for k in broadcast:
                if k == "messages":
                    child_state["messages"] = parent_messages
                elif k == "form":
                    child_state["form"] = parent_form
                elif k == "variables":
                    child_state["variables"] = child_vars
                else:
                    # allow arbitrary broadcast keys if your state carries them
                    if isinstance(state, dict) and k in state:
                        child_state[k] = state[k]

            sends.append(Send(child_id, child_state))

        await JOIN.put(_thread_key(config), group)
        return Command(goto=sends)

    raise ValueError(f"Unknown parallel mode: {mode!r}")


def build_parallel(node_id: str, cfg: Dict[str, Any]) -> Any:
    async def wrapped(state: OrchestrationState, config: RunnableConfig) -> Any:
        return await parallel_execute(state, config, node_id=node_id, cfg=cfg)
    return wrapped


# -----------------------------------------------------------------------------
# REDUCER
# -----------------------------------------------------------------------------

async def reducer_execute(state: OrchestrationState, config: RunnableConfig, *, node_id: str, cfg: Dict[str, Any]) -> Any:
    """
    Reducer contract (matches your IR schema):
      cfg.data.fields = [{ "target": "results", "strategy": "list_concat" }, ...]

    Each child invocation of the reducer receives a child state. The reducer:
      1) reads the fanout stack top to find group_id
      2) accumulates each configured field from the child state (at its 'target' path)
      3) pumps more work (items mode) until completion
      4) once done, returns Command(update=final_update, goto=next_node)

    CRITICAL FIX:
      - NEVER writes 'row' in update
      - rejects any reducer field whose target is 'row' or 'row.*'
      - pops only the TOP fanout stack frame, enabling nested parallel/reducer
    """
    data = (cfg or {}).get("data", {}) or {}
    fields: List[Dict[str, str]] = list(data.get("fields") or [])
    next_node = (data.get("next") or cfg.get("next_node") or cfg.get("next"))  # compiler can inject
    if next_node is not None and not isinstance(next_node, str):
        next_node = None

    vars_ = state.get("variables") or {}
    if not isinstance(vars_, dict):
        raise ValueError("state.variables must be a dict")

    stack = _stack_get(vars_)
    if not stack:
        raise ValueError(f"Reducer '{node_id}' missing variables.{FANOUT_STACK_KEY} frame")

    frame = stack[-1]
    if not isinstance(frame, dict):
        raise ValueError(f"Reducer '{node_id}' invalid fanout frame")

    group_id = frame.get("group_id")
    if not isinstance(group_id, str) or not group_id:
        raise ValueError(f"Reducer '{node_id}' missing frame.group_id")

    tkey = _thread_key(config)
    group = await JOIN.get(tkey, group_id)

    # initialize reduction schema once
    if not group.reduce_fields:
        group.reduce_fields = fields
        for f in fields:
            tgt = f.get("target") or ""
            strat = f.get("strategy") or "last_write_wins"
            if tgt == "row" or tgt.startswith("row."):
                raise ValueError(
                    f"Reducer field target '{tgt}' is not allowed. "
                    "Do not reduce into 'row' (branch-local, non-reducible). Use variables.* or top-level keys."
                )
            group.acc.setdefault(tgt, None)
            # store strategy back so we don't need to look it up later
            f["strategy"] = strat

    # timeout handling
    if group.timeout_sec and (time.time() - group.created_at) > group.timeout_sec:
        if group.on_timeout == "fail":
            await JOIN.delete(tkey, group_id)
            raise TimeoutError(f"Reducer timeout in group {group_id}")
        group.done = True

    # 1) accumulate from THIS child invocation
    for f in group.reduce_fields:
        tgt = f["target"]
        strat = f["strategy"]

        # value is read from the child state at the *same* path we will output to at the end.
        # (This matches your IR pattern where edge mappings write to "results", etc.)
        val = _get_path(state, "$." + tgt) if tgt else None
        group.acc[tgt] = _apply_reduce(strat, group.acc.get(tgt), val)

    # 2) mark this child complete
    group.inflight = max(0, group.inflight - 1)
    group.completed += 1

    # done threshold
    target_done = group.total
    if group.wait_for == "firstN" and group.first_n:
        target_done = min(group.total, group.first_n)
    if group.wait_for == "firstBest":
        target_done = 1

    # 3) pump more work (items mode) WITHOUT updating state
    if not group.done and group.completed < target_done:
        sends: List[Send] = []

        if group.mode == "items":
            if not group.items_target_node:
                raise ValueError("JoinGroup missing items_target_node")
            while group.inflight < group.concurrency and group.pending:
                item = group.pending.popleft()
                group.inflight += 1

                # Each pumped child gets a COPY of variables with the SAME stack frame
                child_vars = dict(vars_)
                child_stack = _stack_get(child_vars)
                if not child_stack or child_stack[-1].get("group_id") != group_id:
                    # enforce current frame present
                    child_stack.append(dict(frame))

                child_state: Dict[str, Any] = {
                    "messages": state.get("messages", []),
                    "form": state.get("form", {}),
                    "variables": child_vars,
                    "row": item,
                }
                sends.append(Send(group.items_target_node, child_state))

        else:
            # branches mode: pump remaining branch nodes (they are stored in group.pending)
            while group.inflight < group.concurrency and group.pending:
                child_id = group.pending.popleft()
                group.inflight += 1

                child_vars = dict(vars_)
                child_stack = _stack_get(child_vars)
                if not child_stack or child_stack[-1].get("group_id") != group_id:
                    child_stack.append(dict(frame))

                child_state = {
                    "messages": state.get("messages", []),
                    "form": state.get("form", {}),
                    "variables": child_vars,
                }
                sends.append(Send(child_id, child_state))

        if sends:
            return Command(goto=sends)
        return Command()

    # 4) finalize: build final_update WITHOUT 'row', pop ONLY the top stack frame
    group.done = True

    final_update: Dict[str, Any] = {}

    for f in group.reduce_fields:
        tgt = f["target"]
        strat = f["strategy"]
        val = group.acc.get(tgt)
        if strat == "avg":
            val = _finalize_avg(val)
        _set_path(final_update, tgt, val)

    # pop stack frame for nested safety
    new_vars = dict(vars_)
    new_stack = list(_stack_get(new_vars))
    if new_stack and isinstance(new_stack[-1], dict) and new_stack[-1].get("group_id") == group_id:
        new_stack.pop()
    new_vars[FANOUT_STACK_KEY] = new_stack

    # merge variables update (still no 'row')
    final_update = _deep_merge(final_update, {"variables": new_vars})

    await JOIN.delete(tkey, group_id)

    goto = next_node or frame.get("next_node") or group.next_node or ()
    return Command(update=final_update, goto=goto)


def build_reducer(node_id: str, cfg: Dict[str, Any]) -> Any:
    async def wrapped(state: OrchestrationState, config: RunnableConfig) -> Any:
        return await reducer_execute(state, config, node_id=node_id, cfg=cfg)
    return wrapped
