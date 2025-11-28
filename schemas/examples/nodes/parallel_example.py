# app_llm_row_agent.py
from __future__ import annotations

import base64
import json
import time
import uuid
import operator
from typing import Annotated, List, Optional, TypedDict, Literal

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, JSONResponse
from pydantic import BaseModel, Field

# LangGraph
from langgraph.graph import StateGraph, START, END
from langgraph.types import Send
from langgraph.checkpoint.memory import MemorySaver

# LangChain (LLM + messages + tools)
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_core.tools import tool
from pydantic import BaseModel as PydBase

app = FastAPI(title="Data Validation Orchestrator (LLM row agent, bounded concurrency)")

# ---------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------
class ExecuteRequest(BaseModel):
    model: str = Field("gpt-4o-mini", description="Model for LLM row agent and optional summary")
    thread_id: str = Field(..., description="Conversation/thread id for resumability")
    file_ref: str = Field(..., description="Blob/file handle (e.g., s3://.., az://.., gcs://..)")
    upload_to_sharepoint: bool = False
    llm_summary: bool = False
    concurrency: int = Field(8, ge=1, le=256)  # bounded degree of parallelism
    stream: bool = True

# ---------------------------------------------------------------------
# State & schemas
# ---------------------------------------------------------------------
class Row(dict):
    """Parsed row payload — demo structure."""

class Finding(PydBase):
    field: str
    message: str
    expected: Optional[str] = None
    actual: Optional[str] = None

class RowResultModel(PydBase):
    row_id: int
    status: Literal["ok", "warn", "error"]
    findings: List[Finding]
    summary: str
    sources: List[str]  # list of s3 keys (or URLs)

class RowResult(TypedDict):
    row_id: int
    status: str
    findings: List[dict]
    summary: str
    sources: List[str]

class OrchestratorState(TypedDict):
    # Config
    model: str
    file_ref: str
    upload_to_sharepoint: bool
    llm_summary: bool
    concurrency: int

    # Planner/worker bookkeeping
    rows: List[Row]
    work_queue: List[Row]
    total_rows: int
    dispatched: int
    completed: Annotated[int, operator.add]  # reducer: add 1 per worker completion

    # Aggregation
    results: Annotated[List[RowResult], operator.add]   # reducer: list concat
    events: Annotated[List[str], operator.add]          # reducer: list concat

    # Artifacts
    report_bytes_b64: Optional[str]
    sharepoint_url: Optional[str]
    final_summary_text: Optional[str]

# ---------------------------------------------------------------------
# Deterministic helpers (stubs for I/O)
# ---------------------------------------------------------------------
async def fetch_file_bytes(file_ref: str) -> bytes:
    """Fetch bytes for file_ref from your blob storage.
    TODO: Replace with real implementation (S3/GCS/Azure).
    """
    return b""

def parse_xlsx_rows(xlsx_bytes: bytes) -> List[Row]:
    """Parse Excel to rows. Replace with openpyxl/pandas."""
    rows: List[Row] = []
    for i in range(1, 37):  # a few more rows to see concurrency at work
        rows.append({"row_id": i, "sku": f"SKU-{i:04d}", "qty": (i * 3) % 11, "price": round(9.5 + (i % 5) * 1.35, 2)})
    return rows

def build_report_bytes(results: List[RowResult]) -> bytes:
    """Build report artifact (CSV for demo; replace with XLSX builder)."""
    header = "row_id,status,issues,sources\n"
    lines = []
    for r in results:
        issues = "; ".join(f"{f['field']}:{f['message']}" for f in r["findings"]) or ""
        sources = ";".join(r.get("sources", []))
        lines.append(f"{r['row_id']},{r['status']},{issues},{sources}")
    return (header + "\n".join(lines) + "\n").encode("utf-8")

async def upload_to_sharepoint_stub(report_bytes: bytes) -> str:
    """Upload to SharePoint (stub). Replace with Microsoft Graph calls."""
    return "https://contoso.sharepoint.com/sites/ops/Shared%20Documents/report.csv"

# ---------------------------------------------------------------------
# S3 tools (stubs) — used by the LLM row agent
# ---------------------------------------------------------------------
class S3ListArgs(PydBase):
    prefix: str
    limit: int = 5

@tool("s3.list", args_schema=S3ListArgs)
def s3_list(prefix: str, limit: int = 5) -> dict:
    """List candidate doc keys under a prefix. (Stub)"""
    keys = [f"{prefix}/doc{i}.txt" for i in range(1, min(limit, 5) + 1)]
    return {"keys": keys}

class S3GetTextArgs(PydBase):
    key: str

@tool("s3.get_text", args_schema=S3GetTextArgs)
def s3_get_text(key: str) -> dict:
    """Fetch plain-text content of an object. (Stub)"""
    demo_text = (
        "Sample unstructured document.\n"
        "Contains SKUs and quantities, e.g., SKU-0001 qty 5, SKU-0002 qty 3.\n"
        f"(This is a stub for {key})"
    )
    return {"key": key, "text": demo_text, "bytes": None}

TOOLS = [s3_list, s3_get_text]

# ---------------------------------------------------------------------
# LLMs
# ---------------------------------------------------------------------
def get_base_llm(model: str):
    return ChatOpenAI(model=model, temperature=0)

# ---------------------------------------------------------------------
# LangGraph nodes
# ---------------------------------------------------------------------
async def ingest(state: OrchestratorState):
    """Download & parse XLSX → queue initialization (deterministic)."""
    bytes_ = await fetch_file_bytes(state["file_ref"])
    rows = parse_xlsx_rows(bytes_)
    ev = [f"📥 fetched file_ref={state['file_ref']}", f"📊 parsed rows: {len(rows)}"]
    return {
        "rows": rows,
        "work_queue": list(rows),      # copy for mutation
        "total_rows": len(rows),
        "dispatched": 0,
        "completed": 0,                # reducer seed
        "events": ev,
    }

def planner(state: OrchestratorState):
    """Bounded scatter: dispatch up to (concurrency - inflight) rows.
    inflight = dispatched - completed
    """
    queue = list(state.get("work_queue", []))
    total = state.get("total_rows", 0)
    dispatched = state.get("dispatched", 0)
    completed = state.get("completed", 0)
    inflight = max(dispatched - completed, 0)
    capacity = max(state["concurrency"] - inflight, 0)

    n = min(capacity, len(queue))
    sends: List[Send] = []
    if n > 0:
        batch = [queue.pop(0) for _ in range(n)]
        for r in batch:
            sends.append(Send("row_agent", {"row": r, "model": state["model"]}))
        dispatched_new = dispatched + n
        ev = [f"🧭 planner: inflight={inflight} cap={capacity} dispatched+={n} ({dispatched_new}/{total})"]
        updates = {"work_queue": queue, "dispatched": dispatched_new, "events": ev}
        return [updates] + sends

    # No capacity or queue empty
    if completed < total:
        # Waiting for workers to finish
        ev = [f"⏳ planner: waiting — inflight={inflight}, completed={completed}/{total}"]
        return {"events": ev}
    else:
        # All done; let combine run next
        ev = [f"✅ planner: all rows completed ({completed}/{total})"]
        return {"events": ev, "ready_to_combine": True}

def _tool_loop(messages, llm_with_tools, max_rounds: int = 4):
    """Standard tool loop for the row agent."""
    from langgraph.prebuilt import ToolNode, tools_condition

    tool_node = ToolNode(TOOLS)
    ai = llm_with_tools.invoke(messages)
    messages = messages + [ai]
    route = tools_condition({"messages": messages})
    rounds = 0
    while route == "tools" and rounds < max_rounds:
        update = tool_node.invoke({"messages": messages})
        messages = messages + update["messages"]
        ai = llm_with_tools.invoke(messages)
        messages = messages + [ai]
        route = tools_condition({"messages": messages})
        rounds += 1
    return messages

def row_agent(state: OrchestratorState):
    """LLM-driven per-row analysis — ALWAYS uses LLM + S3 tools."""
    row: Row = state["row"]
    base_llm = get_base_llm(state["model"])
    llm_with_tools = base_llm.bind_tools(TOOLS)

    system = SystemMessage(content=(
        "You are a data validation agent. Given one tabular row and access to unstructured documents in S3, "
        "you must retrieve the most relevant doc(s) with s3.list and s3.get_text, extract facts, compare to the row, "
        "and prepare to return a strict JSON verdict later."
    ))
    human = HumanMessage(content=json.dumps({"row": row}, ensure_ascii=False))

    messages = [system, human]
    messages = _tool_loop(messages, llm_with_tools, max_rounds=4)

    parser_llm = base_llm.with_structured_output(RowResultModel)
    final = parser_llm.invoke([
        SystemMessage(content=(
            "Return a strict RowResultModel for the previously analyzed row. "
            "status must be one of ok|warn|error. "
            "Use 'sources' as a list of S3 keys you relied on."
        )),
        *messages,
        HumanMessage(content="Return the RowResultModel JSON now.")
    ])
    result: RowResult = final.model_dump()
    ev = f"🧠 row {result['row_id']}: {result['status']} ({len(result['findings'])} finding(s))"
    # Increment 'completed' atomically via reducer by returning +1
    return {"results": [result], "completed": 1, "events": [ev]}

async def combine(state: OrchestratorState):
    """Fan-in: only proceed when all rows completed; otherwise no-op."""
    total = state.get("total_rows", 0)
    completed = state.get("completed", 0)
    if completed < total:
        # not ready yet; return no updates so graph waits for next change
        return {}

    results = state.get("results", [])
    report = build_report_bytes(results)
    b64 = base64.b64encode(report).decode("ascii")
    events = [f"🧮 combined {len(results)} row results into report ({len(report)} bytes)"]

    final_summary_text = None
    if state.get("llm_summary"):
        base_llm = get_base_llm(state["model"])
        bullets = "\n".join(
            f"- Row {r['row_id']}: {r['status']} ({len(r['findings'])} issue[s])"
            for r in results[:30]
        )
        out = base_llm.invoke([
            SystemMessage(content="Summarize the following validation outcomes in ≤120 words."),
            HumanMessage(content=bullets)
        ])
        final_summary_text = str(out.content or "")[:2000]
        events.append("📝 LLM summary generated")
    return {"report_bytes_b64": b64, "final_summary_text": final_summary_text, "events": events}

async def publish(state: OrchestratorState):
    """Upload to SharePoint (stub)."""
    url = None
    if state.get("upload_to_sharepoint"):
        url = await upload_to_sharepoint_stub(base64.b64decode(state.get("report_bytes_b64") or ""))
        ev = f"☁️ uploaded report to SharePoint: {url}"
    else:
        ev = "☁️ upload skipped (checkbox off)"
    return {"sharepoint_url": url, "events": [ev]}

# ---------------------------------------------------------------------
# Build graph
# ---------------------------------------------------------------------
g = StateGraph(OrchestratorState)
g.add_node("ingest", ingest)         # async
g.add_node("planner", planner)       # bounded scatter
g.add_node("row_agent", row_agent)   # LLM + tools (parallel)
g.add_node("combine", combine)       # async
g.add_node("publish", publish)       # async

g.add_edge(START, "ingest")
g.add_edge("ingest", "planner")
g.add_edge("planner", "combine")     # combine will no-op until all done
g.add_edge("planner", "row_agent")   # Send-based fan-out
g.add_edge("row_agent", "planner")   # worker completion → planner tops up
g.add_edge("combine", "publish")
g.add_edge("publish", END)

checkpointer = MemorySaver()
GRAPH_APP = g.compile(checkpointer=checkpointer)

# ---------------------------------------------------------------------
# SSE in OpenAI Chat Completions shape
# ---------------------------------------------------------------------
def chat_chunk(model: str, chat_id: str, created: int, *, role: str | None = None,
               content: str | None = None, finish_reason: str | None = None):
    payload = {
        "id": chat_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": None
        }]
    }
    if role is not None:
        payload["choices"][0]["delta"]["role"] = role
    if content is not None:
        payload["choices"][0]["delta"]["content"] = content
    if finish_reason is not None:
        payload["choices"][0]["finish_reason"] = finish_reason
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

def done_marker():
    return "data: [DONE]\n\n"

# ---------------------------------------------------------------------
# API endpoint – /v1/chat/completions (streaming)
# ---------------------------------------------------------------------
@app.post("/v1/chat/completions")
async def execute(req: ExecuteRequest):
    init: OrchestratorState = {
        "model": req.model,
        "file_ref": req.file_ref,
        "upload_to_sharepoint": req.upload_to_sharepoint,
        "llm_summary": req.llm_summary,
        "concurrency": req.concurrency,

        "rows": [],
        "work_queue": [],
        "total_rows": 0,
        "dispatched": 0,
        "completed": 0,

        "results": [],
        "events": [],

        "report_bytes_b64": None,
        "sharepoint_url": None,
        "final_summary_text": None,
    }

    chat_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async def sse():
        yield chat_chunk(req.model, chat_id, created, role="assistant")
        yield chat_chunk(req.model, chat_id, created, content=f"Starting Data Validation (LLM per row, concurrency={req.concurrency})…\n")

        async for step in GRAPH_APP.astream(
            init,
            config={"configurable": {"thread_id": req.thread_id}}
        ):
            for node, delta in step.items():
                for line in (delta.get("events") or []):
                    yield chat_chunk(req.model, chat_id, created, content=f"{line}\n")

                if node == "combine" and req.llm_summary and delta.get("final_summary_text"):
                    yield chat_chunk(req.model, chat_id, created, content="\n--- Summary ---\n")
                    yield chat_chunk(req.model, chat_id, created, content=str(delta["final_summary_text"]) + "\n")

                if node == "publish" and delta.get("sharepoint_url"):
                    yield chat_chunk(req.model, chat_id, created, content=f"\nReport URL: {delta['sharepoint_url']}\n")

        yield chat_chunk(req.model, chat_id, created, finish_reason="stop")
        yield done_marker()

    if req.stream:
        return StreamingResponse(sse(), media_type="text/event-stream")
    else:
        out = await GRAPH_APP.ainvoke(init, config={"configurable": {"thread_id": req.thread_id}})
        lines = out.get("events", []) or []
        if out.get("final_summary_text"):
            lines += ["", "--- Summary ---", out["final_summary_text"]]
        if out.get("sharepoint_url"):
            lines += ["", f"Report URL: {out['sharepoint_url']}"]
        content = "\n".join(lines) or "Done."
        resp = {
            "id": chat_id,
            "object": "chat.completion",
            "created": created,
            "model": req.model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop"
            }]
        }
        return JSONResponse(resp)

# Local dev:
# uvicorn app_llm_row_agent:app --reload
