from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, TypedDict, Annotated, Literal, Tuple
import operator

import pyodbc

from langchain_openai import ChatOpenAI
from langchain_core.messages import (
    AnyMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)
from langchain_core.tools import tool

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Send

# ----------------------------
# Configuration
# ----------------------------

DB_CONN_STR = os.environ.get("DB_CONN_STR", "")
if not DB_CONN_STR:
    raise RuntimeError("Missing DB_CONN_STR env var.")

LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-4.1-mini")
PROFILE_SAMPLE_ROWS = int(os.environ.get("PROFILE_SAMPLE_ROWS", "25"))
PROFILE_TOP_VALUES = int(os.environ.get("PROFILE_TOP_VALUES", "15"))
JOIN_VERIFY_SAMPLE_KEYS = int(os.environ.get("JOIN_VERIFY_SAMPLE_KEYS", "5000"))

# Safety limits
MAX_SQL_CHARS = 20000
FORBIDDEN_SQL = re.compile(
    r"\b(INSERT|UPDATE|DELETE|MERGE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|EXEC|EXECUTE)\b",
    re.IGNORECASE,
)

# ----------------------------
# DB helpers (read-only, templated)
# ----------------------------

@dataclass
class Db:
    conn_str: str

    def connect(self) -> pyodbc.Connection:
        # autocommit for read-only SELECT usage
        return pyodbc.connect(self.conn_str, autocommit=True)

    def query(self, sql: str, params: Optional[Tuple[Any, ...]] = None) -> List[Dict[str, Any]]:
        sql = sql.strip()
        if len(sql) > MAX_SQL_CHARS:
            raise ValueError("SQL too long.")
        if FORBIDDEN_SQL.search(sql):
            raise ValueError("Forbidden SQL detected (write/ddl/exec).")
        if not sql.lower().startswith("select"):
            raise ValueError("Only SELECT statements are allowed.")

        with self.connect() as conn:
            cur = conn.cursor()
            # reduce locking impact
            cur.execute("SET TRANSACTION ISOLATION LEVEL READ UNCOMMITTED;")
            cur.execute("SET LOCK_TIMEOUT 5000;")
            cur.execute(sql, params or ())
            cols = [c[0] for c in cur.description]
            rows = cur.fetchall()
            out: List[Dict[str, Any]] = []
            for r in rows:
                out.append({cols[i]: r[i] for i in range(len(cols))})
            return out


db = Db(DB_CONN_STR)

# ----------------------------
# Tools (@tool)
# ----------------------------

@tool
def get_catalog(schema_like: str = "%") -> Dict[str, Any]:
    """
    Get a high-level catalog of tables and views in the database for schemas matching schema_like (SQL LIKE pattern).
    Returns: schemas, tables, views.
    """
    schemas = db.query(
        """
        SELECT s.name AS schema_name
        FROM sys.schemas s
        WHERE s.name LIKE ?
        ORDER BY s.name
        """,
        (schema_like,),
    )

    tables = db.query(
        """
        SELECT s.name AS schema_name, t.name AS object_name, 'TABLE' AS object_type
        FROM sys.tables t
        JOIN sys.schemas s ON s.schema_id = t.schema_id
        WHERE s.name LIKE ?
        ORDER BY s.name, t.name
        """,
        (schema_like,),
    )

    views = db.query(
        """
        SELECT s.name AS schema_name, v.name AS object_name, 'VIEW' AS object_type
        FROM sys.views v
        JOIN sys.schemas s ON s.schema_id = v.schema_id
        WHERE s.name LIKE ?
        ORDER BY s.name, v.name
        """,
        (schema_like,),
    )

    return {"schemas": schemas, "tables": tables, "views": views}


@tool
def get_object_metadata(schema_name: str, object_name: str) -> Dict[str, Any]:
    """
    Get detailed metadata for a given table or view:
    columns, keys, foreign keys, indexes, and MS_Description extended properties (if present).
    """
    # object_id scoped to schema + name
    obj = db.query(
        """
        SELECT o.object_id, o.type_desc
        FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        WHERE s.name = ? AND o.name = ? AND o.type IN ('U','V')
        """,
        (schema_name, object_name),
    )
    if not obj:
        return {"error": "Object not found."}

    object_id = obj[0]["object_id"]
    type_desc = obj[0]["type_desc"]

    columns = db.query(
        """
        SELECT
            c.column_id,
            c.name AS column_name,
            t.name AS data_type,
            c.max_length,
            c.precision,
            c.scale,
            c.is_nullable,
            c.is_identity,
            c.is_computed,
            dc.definition AS default_definition,
            ep.value AS ms_description
        FROM sys.columns c
        JOIN sys.types t ON t.user_type_id = c.user_type_id
        LEFT JOIN sys.default_constraints dc ON dc.parent_object_id = c.object_id AND dc.parent_column_id = c.column_id
        LEFT JOIN sys.extended_properties ep
            ON ep.major_id = c.object_id AND ep.minor_id = c.column_id AND ep.name = 'MS_Description'
        WHERE c.object_id = ?
        ORDER BY c.column_id
        """,
        (object_id,),
    )

    # primary key / unique constraints
    keys = db.query(
        """
        SELECT
            kc.type_desc AS key_type,
            kc.name AS constraint_name,
            ic.key_ordinal,
            col.name AS column_name
        FROM sys.key_constraints kc
        JOIN sys.index_columns ic ON ic.object_id = kc.parent_object_id AND ic.index_id = kc.unique_index_id
        JOIN sys.columns col ON col.object_id = ic.object_id AND col.column_id = ic.column_id
        WHERE kc.parent_object_id = ?
        ORDER BY kc.type_desc, kc.name, ic.key_ordinal
        """,
        (object_id,),
    )

    # foreign keys (outbound)
    fks = db.query(
        """
        SELECT
            fk.name AS fk_name,
            sch1.name AS from_schema, tab1.name AS from_table, col1.name AS from_column,
            sch2.name AS to_schema,   tab2.name AS to_table,   col2.name AS to_column
        FROM sys.foreign_key_columns fkc
        JOIN sys.foreign_keys fk ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables tab1 ON tab1.object_id = fkc.parent_object_id
        JOIN sys.schemas sch1 ON sch1.schema_id = tab1.schema_id
        JOIN sys.columns col1 ON col1.object_id = tab1.object_id AND col1.column_id = fkc.parent_column_id
        JOIN sys.tables tab2 ON tab2.object_id = fkc.referenced_object_id
        JOIN sys.schemas sch2 ON sch2.schema_id = tab2.schema_id
        JOIN sys.columns col2 ON col2.object_id = tab2.object_id AND col2.column_id = fkc.referenced_column_id
        WHERE fkc.parent_object_id = ?
        ORDER BY fk.name, col1.column_id
        """,
        (object_id,),
    )

    # inbound FKs referencing this object (only for tables)
    inbound_fks = db.query(
        """
        SELECT
            fk.name AS fk_name,
            sch1.name AS from_schema, tab1.name AS from_table, col1.name AS from_column,
            sch2.name AS to_schema,   tab2.name AS to_table,   col2.name AS to_column
        FROM sys.foreign_key_columns fkc
        JOIN sys.foreign_keys fk ON fk.object_id = fkc.constraint_object_id
        JOIN sys.tables tab1 ON tab1.object_id = fkc.parent_object_id
        JOIN sys.schemas sch1 ON sch1.schema_id = tab1.schema_id
        JOIN sys.columns col1 ON col1.object_id = tab1.object_id AND col1.column_id = fkc.parent_column_id
        JOIN sys.tables tab2 ON tab2.object_id = fkc.referenced_object_id
        JOIN sys.schemas sch2 ON sch2.schema_id = tab2.schema_id
        JOIN sys.columns col2 ON col2.object_id = tab2.object_id AND col2.column_id = fkc.referenced_column_id
        WHERE fkc.referenced_object_id = ?
        ORDER BY fk.name, col1.column_id
        """,
        (object_id,),
    )

    # view definition if applicable (can be large; keep it available but optional for prompts)
    view_definition = None
    if type_desc == "VIEW":
        vd = db.query(
            """
            SELECT m.definition
            FROM sys.sql_modules m
            WHERE m.object_id = ?
            """,
            (object_id,),
        )
        if vd:
            view_definition = vd[0]["definition"]

    # object-level description
    obj_desc = db.query(
        """
        SELECT ep.value AS ms_description
        FROM sys.extended_properties ep
        WHERE ep.major_id = ? AND ep.minor_id = 0 AND ep.name = 'MS_Description'
        """,
        (object_id,),
    )

    return {
        "schema_name": schema_name,
        "object_name": object_name,
        "object_type": type_desc,
        "object_ms_description": obj_desc[0]["ms_description"] if obj_desc else None,
        "columns": columns,
        "keys": keys,
        "foreign_keys_outbound": fks,
        "foreign_keys_inbound": inbound_fks,
        "view_definition": view_definition,
    }


@tool
def profile_table(schema_name: str, object_name: str, sample_rows: int = PROFILE_SAMPLE_ROWS) -> Dict[str, Any]:
    """
    Profile a table or view: rowcount (approx for tables), sample rows, and basic completeness hints.
    For views, rowcount may be expensive; we only do TOP sample and skip full count.
    """
    # detect if table or view
    obj = db.query(
        """
        SELECT o.type_desc
        FROM sys.objects o
        JOIN sys.schemas s ON s.schema_id = o.schema_id
        WHERE s.name = ? AND o.name = ? AND o.type IN ('U','V')
        """,
        (schema_name, object_name),
    )
    if not obj:
        return {"error": "Object not found."}
    type_desc = obj[0]["type_desc"]

    fq = f"[{schema_name}].[{object_name}]"

    # Table row count from sys.partitions (fast-ish)
    row_count = None
    if type_desc == "USER_TABLE":
        rc = db.query(
            """
            SELECT SUM(p.rows) AS row_count
            FROM sys.tables t
            JOIN sys.partitions p ON p.object_id = t.object_id
            JOIN sys.schemas s ON s.schema_id = t.schema_id
            WHERE s.name = ? AND t.name = ? AND p.index_id IN (0,1)
            """,
            (schema_name, object_name),
        )
        row_count = int(rc[0]["row_count"]) if rc and rc[0]["row_count"] is not None else None

    # sample rows
    # Avoid ORDER BY for performance. TOP sample is fine for documentation.
    samples = db.query(
        f"""
        SELECT TOP ({int(sample_rows)}) *
        FROM {fq}
        """,
    )

    return {
        "schema_name": schema_name,
        "object_name": object_name,
        "object_type": type_desc,
        "row_count": row_count,
        "sample_rows": samples,
    }


@tool
def profile_column(schema_name: str, object_name: str, column_name: str, top_n: int = PROFILE_TOP_VALUES) -> Dict[str, Any]:
    """
    Profile a column: null %, distinct approx, min/max for numeric/date, avg length for strings, top values (when sensible).
    Uses templates that are reasonably safe but still potentially heavy on huge tables.
    """
    fq = f"[{schema_name}].[{object_name}]"
    col = f"[{column_name}]"

    # null count & row count (using COUNT(*) and SUM CASE) - may be heavy but is standard
    counts = db.query(
        f"""
        SELECT
            COUNT_BIG(1) AS row_count,
            SUM(CASE WHEN {col} IS NULL THEN 1 ELSE 0 END) AS null_count
        FROM {fq}
        """
    )
    row_count = int(counts[0]["row_count"])
    null_count = int(counts[0]["null_count"]) if counts[0]["null_count"] is not None else 0
    null_pct = (null_count / row_count) if row_count else None

    # distinct estimate (approx distinct is possible via APPROX_COUNT_DISTINCT in SQL Server 2019+)
    distinct_est = db.query(
        f"""
        SELECT APPROX_COUNT_DISTINCT({col}) AS approx_distinct
        FROM {fq}
        """
    )
    approx_distinct = int(distinct_est[0]["approx_distinct"]) if distinct_est and distinct_est[0]["approx_distinct"] is not None else None

    # type-aware stats: try MIN/MAX and AVG length; if invalid type, they may error.
    # We keep it simple: attempt generic MIN/MAX and string length in separate queries.
    min_max = None
    try:
        mm = db.query(
            f"""
            SELECT MIN({col}) AS min_value, MAX({col}) AS max_value
            FROM {fq}
            WHERE {col} IS NOT NULL
            """
        )
        min_max = {"min": mm[0]["min_value"], "max": mm[0]["max_value"]}
    except Exception:
        min_max = None

    avg_len = None
    try:
        al = db.query(
            f"""
            SELECT AVG(CAST(LEN(CAST({col} AS NVARCHAR(4000))) AS FLOAT)) AS avg_len
            FROM {fq}
            WHERE {col} IS NOT NULL
            """
        )
        avg_len = float(al[0]["avg_len"]) if al and al[0]["avg_len"] is not None else None
    except Exception:
        avg_len = None

    # sample distinct values
    samples = db.query(
        f"""
        SELECT TOP (10) {col} AS value
        FROM {fq}
        WHERE {col} IS NOT NULL
        """
    )
    sample_values = [r["value"] for r in samples]

    # top values (only if likely enum-ish)
    top_values = []
    if approx_distinct is not None and approx_distinct <= 2000:
        tv = db.query(
            f"""
            SELECT TOP ({int(top_n)}) {col} AS value, COUNT_BIG(1) AS cnt
            FROM {fq}
            WHERE {col} IS NOT NULL
            GROUP BY {col}
            ORDER BY cnt DESC
            """
        )
        top_values = tv

    return {
        "schema_name": schema_name,
        "object_name": object_name,
        "column_name": column_name,
        "row_count": row_count,
        "null_count": null_count,
        "null_pct": null_pct,
        "approx_distinct": approx_distinct,
        "min_max": min_max,
        "avg_len": avg_len,
        "sample_values": sample_values,
        "top_values": top_values,
    }


@tool
def verify_join(
    left_schema: str,
    left_object: str,
    left_key: str,
    right_schema: str,
    right_object: str,
    right_key: str,
    sample_keys: int = JOIN_VERIFY_SAMPLE_KEYS,
) -> Dict[str, Any]:
    """
    Verify a proposed join A.left_key -> B.right_key:
    - match coverage: % of sampled distinct left keys that exist in right
    - right uniqueness: duplicates in right key
    - rough cardinality hints
    """
    left_fq = f"[{left_schema}].[{left_object}]"
    right_fq = f"[{right_schema}].[{right_object}]"
    lk = f"[{left_key}]"
    rk = f"[{right_key}]"

    # Sample distinct keys from left
    sampled = db.query(
        f"""
        WITH sample_left AS (
            SELECT TOP ({int(sample_keys)}) {lk} AS k
            FROM {left_fq}
            WHERE {lk} IS NOT NULL
            GROUP BY {lk}
        )
        SELECT COUNT_BIG(1) AS sampled_keys
        FROM sample_left
        """
    )
    sampled_keys = int(sampled[0]["sampled_keys"]) if sampled else 0
    if sampled_keys == 0:
        return {"sampled_keys": 0, "match_rate": None, "right_duplicates": None, "notes": "No non-null keys sampled on left."}

    # Match rate: how many of those sampled keys exist in right
    matches = db.query(
        f"""
        WITH sample_left AS (
            SELECT TOP ({int(sample_keys)}) {lk} AS k
            FROM {left_fq}
            WHERE {lk} IS NOT NULL
            GROUP BY {lk}
        )
        SELECT
            COUNT_BIG(1) AS sampled_keys,
            SUM(CASE WHEN EXISTS (
                SELECT 1 FROM {right_fq} r WHERE r.{rk} = sample_left.k
            ) THEN 1 ELSE 0 END) AS matched_keys
        FROM sample_left
        """
    )
    matched_keys = int(matches[0]["matched_keys"]) if matches and matches[0]["matched_keys"] is not None else 0
    match_rate = matched_keys / sampled_keys if sampled_keys else None

    # Right key duplicates (uniqueness)
    # If this is huge, it may be expensive; it’s still a standard check.
    dup = db.query(
        f"""
        SELECT SUM(CASE WHEN cnt > 1 THEN 1 ELSE 0 END) AS duplicate_key_count
        FROM (
            SELECT {rk} AS k, COUNT_BIG(1) AS cnt
            FROM {right_fq}
            WHERE {rk} IS NOT NULL
            GROUP BY {rk}
        ) d
        """
    )
    right_duplicates = int(dup[0]["duplicate_key_count"]) if dup and dup[0]["duplicate_key_count"] is not None else None

    return {
        "sampled_keys": sampled_keys,
        "matched_keys": matched_keys,
        "match_rate": match_rate,
        "right_duplicate_key_count": right_duplicates,
        "interpretation": {
            "coverage": "high" if match_rate is not None and match_rate >= 0.98 else "medium" if match_rate and match_rate >= 0.8 else "low",
            "right_key_unique": (right_duplicates == 0) if right_duplicates is not None else None,
        },
    }


# ----------------------------
# LangGraph state
# ----------------------------

class DDState(TypedDict, total=False):
    # Conversation / agent loop messages
    messages: Annotated[List[AnyMessage], operator.add]

    # Catalog + metadata
    catalog: Dict[str, Any]                         # from get_catalog
    object_metadata: Dict[str, Dict[str, Any]]      # key = "schema.object"

    # Profiles
    table_profiles: Dict[str, Dict[str, Any]]       # key = "schema.object"
    column_profiles: Dict[str, Dict[str, Any]]      # key = "schema.object.column"

    # LLM-produced dictionary content
    table_docs: Dict[str, Dict[str, Any]]           # key = "schema.object"
    join_candidates: List[Dict[str, Any]]           # proposed joins (LLM)
    join_verifications: List[Dict[str, Any]]        # results from verify_join

    # QA findings
    issues: List[Dict[str, Any]]

    # Final outputs
    final_markdown: str
    final_json: Dict[str, Any]


def obj_key(schema: str, name: str) -> str:
    return f"{schema}.{name}"


def col_key(schema: str, name: str, col: str) -> str:
    return f"{schema}.{name}.{col}"


# ----------------------------
# LLM + tool loop scaffolding
# ----------------------------

TOOLS = [get_catalog, get_object_metadata, profile_table, profile_column, verify_join]
tool_node = ToolNode(TOOLS)

llm = ChatOpenAI(model=LLM_MODEL, temperature=0)

SYSTEM_POLICY = """You are building a high-quality SQL Server data dictionary.

Rules:
- Prefer determinism: use tool outputs as ground truth.
- When describing semantics, cite evidence from profiles (null_pct, top_values, min_max, sample_values, keys, FKs).
- Clearly label claims as: Confirmed (from metadata/profiles), Verified (from verify_join), or Hypothesis (LLM inference not yet verified).
- Propose candidate joins beyond declared FKs ONLY when naming/profile evidence suggests it.
- Keep output concise, structured, and professional.
"""


def llm_call(state: DDState, user_prompt: str) -> Dict[str, Any]:
    msgs = state.get("messages", [])
    msgs = msgs + [SystemMessage(content=SYSTEM_POLICY), HumanMessage(content=user_prompt)]
    resp = llm.bind_tools(TOOLS).invoke(msgs)
    return {"messages": [resp]}


# ----------------------------
# Graph nodes
# ----------------------------

def n_bootstrap(state: DDState) -> Dict[str, Any]:
    # Initialize containers
    return {
        "messages": [],
        "object_metadata": {},
        "table_profiles": {},
        "column_profiles": {},
        "table_docs": {},
        "join_candidates": [],
        "join_verifications": [],
        "issues": [],
    }


def n_discover_catalog_llm(state: DDState) -> Dict[str, Any]:
    # We ask the LLM to call get_catalog. ToolNode will execute it.
    return llm_call(
        state,
        "Call get_catalog(schema_like='%') to list all schemas, tables, and views. "
        "Then wait for tool results.",
    )


def n_extract_catalog(state: DDState) -> Dict[str, Any]:
    """
    Pull get_catalog result out of ToolMessages and store in state.catalog.
    """
    catalog = state.get("catalog")
    if catalog:
        return {}

    # Find the most recent ToolMessage from get_catalog
    for m in reversed(state.get("messages", [])):
        if isinstance(m, ToolMessage) and m.name == "get_catalog":
            payload = json.loads(m.content)
            return {"catalog": payload}
    return {"issues": state.get("issues", []) + [{"type": "catalog_missing", "detail": "No get_catalog tool output found."}]}


def n_dispatch_object_metadata(state: DDState) -> Send:
    """
    Fan-out: request get_object_metadata for each table/view.
    """
    catalog = state.get("catalog", {})
    objs = (catalog.get("tables", []) or []) + (catalog.get("views", []) or [])

    sends: List[Send] = []
    for o in objs:
        k = obj_key(o["schema_name"], o["object_name"])
        if k in state.get("object_metadata", {}):
            continue
        sends.append(Send("fetch_object_metadata_llm", {"target_schema": o["schema_name"], "target_object": o["object_name"]}))

    # If nothing to do, move on
    return Send("after_object_metadata_fetched", {}) if not sends else Send("fanout_object_metadata", {"sends": sends})


def n_fanout_object_metadata(state: Dict[str, Any]) -> Dict[str, Any]:
    # This node exists just to return Sends stored in state.
    return {"goto": state["sends"]}


def n_fetch_object_metadata_llm(state: DDState) -> Dict[str, Any]:
    schema = state["target_schema"]
    obj = state["target_object"]
    return llm_call(
        state,
        f"Call get_object_metadata(schema_name='{schema}', object_name='{obj}') and wait for tool results.",
    )


def n_store_object_metadata(state: DDState) -> Dict[str, Any]:
    """
    Capture tool output and store in object_metadata dict.
    """
    schema = state.get("target_schema")
    obj = state.get("target_object")
    if not schema or not obj:
        return {}

    k = obj_key(schema, obj)
    meta_map = dict(state.get("object_metadata", {}))

    # Find the most recent ToolMessage from get_object_metadata
    for m in reversed(state.get("messages", [])):
        if isinstance(m, ToolMessage) and m.name == "get_object_metadata":
            payload = json.loads(m.content)
            meta_map[k] = payload
            break

    return {"object_metadata": meta_map}


def n_after_object_metadata_fetched(state: DDState) -> Dict[str, Any]:
    # no-op marker
    return {}


def n_dispatch_table_profiles(state: DDState) -> Send:
    """
    Fan-out table/view profiling.
    """
    catalog = state.get("catalog", {})
    objs = (catalog.get("tables", []) or []) + (catalog.get("views", []) or [])

    sends: List[Send] = []
    for o in objs:
        k = obj_key(o["schema_name"], o["object_name"])
        if k in state.get("table_profiles", {}):
            continue
        sends.append(Send("profile_table_llm", {"target_schema": o["schema_name"], "target_object": o["object_name"]}))

    return Send("after_table_profiles_fetched", {}) if not sends else Send("fanout_table_profiles", {"sends": sends})


def n_fanout_table_profiles(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"goto": state["sends"]}


def n_profile_table_llm(state: DDState) -> Dict[str, Any]:
    schema = state["target_schema"]
    obj = state["target_object"]
    return llm_call(
        state,
        f"Call profile_table(schema_name='{schema}', object_name='{obj}', sample_rows={PROFILE_SAMPLE_ROWS}) and wait for tool results.",
    )


def n_store_table_profile(state: DDState) -> Dict[str, Any]:
    schema = state.get("target_schema")
    obj = state.get("target_object")
    if not schema or not obj:
        return {}

    k = obj_key(schema, obj)
    prof_map = dict(state.get("table_profiles", {}))

    for m in reversed(state.get("messages", [])):
        if isinstance(m, ToolMessage) and m.name == "profile_table":
            payload = json.loads(m.content)
            prof_map[k] = payload
            break

    return {"table_profiles": prof_map}


def n_after_table_profiles_fetched(state: DDState) -> Dict[str, Any]:
    return {}


def n_dispatch_column_profiles(state: DDState) -> Send:
    """
    Fan-out column profiling for every column of every object.
    WARNING: For huge DBs this can be heavy. You may want to:
    - restrict schemas
    - skip very large tables
    - or profile only "important" columns first.
    """
    meta_map = state.get("object_metadata", {})
    col_prof_map = state.get("column_profiles", {})
    sends: List[Send] = []

    for ok, meta in meta_map.items():
        if not meta or meta.get("error"):
            continue
        schema, obj = meta["schema_name"], meta["object_name"]
        for c in meta.get("columns", []):
            ck = col_key(schema, obj, c["column_name"])
            if ck in col_prof_map:
                continue
            sends.append(Send("profile_column_llm", {"target_schema": schema, "target_object": obj, "target_column": c["column_name"]}))

    return Send("after_column_profiles_fetched", {}) if not sends else Send("fanout_column_profiles", {"sends": sends})


def n_fanout_column_profiles(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"goto": state["sends"]}


def n_profile_column_llm(state: DDState) -> Dict[str, Any]:
    schema = state["target_schema"]
    obj = state["target_object"]
    col = state["target_column"]
    return llm_call(
        state,
        f"Call profile_column(schema_name='{schema}', object_name='{obj}', column_name='{col}', top_n={PROFILE_TOP_VALUES}) and wait for tool results.",
    )


def n_store_column_profile(state: DDState) -> Dict[str, Any]:
    schema = state.get("target_schema")
    obj = state.get("target_object")
    col = state.get("target_column")
    if not schema or not obj or not col:
        return {}

    ck = col_key(schema, obj, col)
    col_prof_map = dict(state.get("column_profiles", {}))

    for m in reversed(state.get("messages", [])):
        if isinstance(m, ToolMessage) and m.name == "profile_column":
            payload = json.loads(m.content)
            col_prof_map[ck] = payload
            break

    return {"column_profiles": col_prof_map}


def n_after_column_profiles_fetched(state: DDState) -> Dict[str, Any]:
    return {}


def n_write_table_docs(state: DDState) -> Dict[str, Any]:
    """
    LLM writes human-readable dictionary content per table/view.
    We do this in one pass over all objects. For very large DBs, you can fan out per object.
    """
    meta_map = state.get("object_metadata", {})
    table_profiles = state.get("table_profiles", {})
    column_profiles = state.get("column_profiles", {})

    # Build compact packets for the model (don’t dump everything blindly)
    packets: List[Dict[str, Any]] = []
    for ok, meta in meta_map.items():
        if not meta or meta.get("error"):
            continue
        schema, obj = meta["schema_name"], meta["object_name"]
        k = obj_key(schema, obj)
        cols = []
        for c in meta.get("columns", []):
            ck = col_key(schema, obj, c["column_name"])
            cp = column_profiles.get(ck)
            cols.append({
                "column": c,
                "profile": {
                    "null_pct": cp.get("null_pct") if cp else None,
                    "approx_distinct": cp.get("approx_distinct") if cp else None,
                    "min_max": cp.get("min_max") if cp else None,
                    "avg_len": cp.get("avg_len") if cp else None,
                    "sample_values": (cp.get("sample_values") if cp else None),
                    "top_values": (cp.get("top_values") if cp else None),
                }
            })

        packets.append({
            "object_key": ok,
            "metadata": {
                "schema_name": schema,
                "object_name": obj,
                "object_type": meta.get("object_type"),
                "object_ms_description": meta.get("object_ms_description"),
                "keys": meta.get("keys"),
                "foreign_keys_outbound": meta.get("foreign_keys_outbound"),
                "foreign_keys_inbound": meta.get("foreign_keys_inbound"),
            },
            "table_profile": {
                "row_count": table_profiles.get(k, {}).get("row_count"),
                "sample_rows": table_profiles.get(k, {}).get("sample_rows", [])[:10],  # cap
            },
            "columns": cols,
        })

    prompt = (
        "You are given object packets (metadata + profiles). "
        "For EACH object, produce JSON with:\n"
        "- object_key\n"
        "- table_description (short)\n"
        "- grain (what a row represents; if unknown say unknown)\n"
        "- column_descriptions: list of {column_name, description, pii_risk(low/med/high), enum_mapping(if applicable), notes}\n"
        "- confirmed_relationships: declared FKs summarized\n"
        "- inferred_join_candidates: list of join suggestions with 'hypothesis' status and rationale referencing evidence\n\n"
        "Return a single JSON array, nothing else.\n\n"
        f"PACKETS:\n{json.dumps(packets)[:120000]}"
    )

    resp = llm.invoke([SystemMessage(content=SYSTEM_POLICY), HumanMessage(content=prompt)])
    text = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)

    # Parse JSON safely
    try:
        docs = json.loads(text)
    except Exception:
        # fallback: store as issue and keep raw
        return {"issues": state.get("issues", []) + [{"type": "llm_parse_error", "detail": "Failed to parse table docs JSON", "raw": text[:2000]}]}

    table_docs: Dict[str, Dict[str, Any]] = {}
    join_candidates: List[Dict[str, Any]] = list(state.get("join_candidates", []))

    for d in docs:
        ok = d.get("object_key")
        if ok:
            table_docs[ok] = d
            for jc in d.get("inferred_join_candidates", []) or []:
                # normalize to include source table
                jc = dict(jc)
                jc["source_object_key"] = ok
                join_candidates.append(jc)

    return {"table_docs": table_docs, "join_candidates": join_candidates}


def n_verify_joins_dispatch(state: DDState) -> Send:
    """
    Fan-out join verifications for inferred candidates.
    """
    candidates = state.get("join_candidates", [])
    verifications = state.get("join_verifications", [])
    already = {
        (
            v.get("left_schema"), v.get("left_object"), v.get("left_key"),
            v.get("right_schema"), v.get("right_object"), v.get("right_key"),
        )
        for v in verifications
    }

    sends: List[Send] = []
    for jc in candidates:
        # Expect candidates like:
        # {left_schema,left_object,left_key,right_schema,right_object,right_key, rationale, confidence?}
        tup = (
            jc.get("left_schema"), jc.get("left_object"), jc.get("left_key"),
            jc.get("right_schema"), jc.get("right_object"), jc.get("right_key"),
        )
        if None in tup:
            continue
        if tup in already:
            continue
        sends.append(Send("verify_join_llm", {"join_candidate": jc}))

    return Send("after_join_verifications", {}) if not sends else Send("fanout_join_verifications", {"sends": sends})


def n_fanout_join_verifications(state: Dict[str, Any]) -> Dict[str, Any]:
    return {"goto": state["sends"]}


def n_verify_join_llm(state: DDState) -> Dict[str, Any]:
    jc = state["join_candidate"]
    return llm_call(
        state,
        "Call verify_join with these parameters:\n"
        f"{json.dumps(jc)}\n"
        "Wait for tool results.",
    )


def n_store_join_verification(state: DDState) -> Dict[str, Any]:
    jc = state.get("join_candidate")
    if not jc:
        return {}

    for m in reversed(state.get("messages", [])):
        if isinstance(m, ToolMessage) and m.name == "verify_join":
            payload = json.loads(m.content)
            merged = dict(jc)
            merged["verification"] = payload
            verifications = list(state.get("join_verifications", []))
            verifications.append(merged)
            return {"join_verifications": verifications}

    return {}


def n_after_join_verifications(state: DDState) -> Dict[str, Any]:
    return {}


def n_qa_pass(state: DDState) -> Dict[str, Any]:
    """
    LLM checks for inconsistencies and produces issues list.
    """
    prompt = (
        "Perform a QA pass over the generated dictionary content.\n"
        "Look for:\n"
        "- columns described as enums but approx_distinct is large\n"
        "- keys described as unique but duplicates likely\n"
        "- relationships with low match_rate\n"
        "- suspicious PII classifications\n"
        "Return JSON array of issues with fields: {severity, object_key, column_name(optional), issue, evidence, recommendation}.\n\n"
        f"TABLE_DOCS:\n{json.dumps(state.get('table_docs', {}))[:90000]}\n\n"
        f"JOIN_VERIFICATIONS:\n{json.dumps(state.get('join_verifications', []))[:90000]}"
    )
    resp = llm.invoke([SystemMessage(content=SYSTEM_POLICY), HumanMessage(content=prompt)])
    text = resp.content if isinstance(resp.content, str) else json.dumps(resp.content)

    try:
        issues = json.loads(text)
    except Exception:
        issues = state.get("issues", []) + [{"type": "llm_parse_error", "detail": "Failed to parse QA JSON", "raw": str(text)[:2000]}]

    return {"issues": issues}


def n_render_outputs(state: DDState) -> Dict[str, Any]:
    """
    Render Markdown + JSON outputs.
    """
    out_json = {
        "catalog": state.get("catalog"),
        "object_metadata": state.get("object_metadata"),
        "table_profiles": state.get("table_profiles"),
        "column_profiles": state.get("column_profiles"),
        "table_docs": state.get("table_docs"),
        "join_verifications": state.get("join_verifications"),
        "issues": state.get("issues"),
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model": LLM_MODEL,
    }

    # Markdown rendering (simple, you can enhance)
    md_lines: List[str] = []
    md_lines.append("# Data Dictionary\n")
    md_lines.append(f"- Generated: {out_json['generated_at_utc']}\n")
    md_lines.append(f"- Model: {LLM_MODEL}\n")

    issues = state.get("issues", [])
    if issues:
        md_lines.append("\n## Issues / QA Findings\n")
        for it in issues[:200]:
            sev = it.get("severity", it.get("type", "issue"))
            obj = it.get("object_key", "")
            col = it.get("column_name", "")
            md_lines.append(f"- **{sev}** `{obj}` `{col}` — {it.get('issue', it.get('detail',''))}\n")

    table_docs = state.get("table_docs", {})
    meta_map = state.get("object_metadata", {})
    col_prof = state.get("column_profiles", {})

    md_lines.append("\n## Objects\n")
    for ok in sorted(table_docs.keys()):
        doc = table_docs[ok]
        meta = meta_map.get(ok, {})
        schema = meta.get("schema_name")
        name = meta.get("object_name")
        md_lines.append(f"\n### `{ok}`\n")
        md_lines.append(f"- Type: {meta.get('object_type')}\n")
        if meta.get("object_ms_description"):
            md_lines.append(f"- MS_Description: {meta.get('object_ms_description')}\n")
        md_lines.append(f"\n**Description:** {doc.get('table_description','')}\n\n")
        md_lines.append(f"**Grain:** {doc.get('grain','unknown')}\n\n")

        # Relationships
        md_lines.append("**Declared FKs:**\n")
        fks = meta.get("foreign_keys_outbound") or []
        if not fks:
            md_lines.append("- (none)\n")
        else:
            for fk in fks[:200]:
                md_lines.append(
                    f"- {fk['fk_name']}: {fk['from_schema']}.{fk['from_table']}.{fk['from_column']} "
                    f"→ {fk['to_schema']}.{fk['to_table']}.{fk['to_column']}\n"
                )

        # Columns table
        md_lines.append("\n**Columns:**\n\n")
        md_lines.append("| Column | Type | Nullable | Description | Evidence |\n")
        md_lines.append("|---|---|---:|---|---|\n")

        cols = (meta.get("columns") or [])
        col_descs = {c["column_name"]: c for c in (doc.get("column_descriptions") or [])}
        for c in cols:
            cn = c["column_name"]
            dt = c["data_type"]
            nullable = "Y" if c.get("is_nullable") else "N"
            desc = (col_descs.get(cn) or {}).get("description", "")
            ck = col_key(schema, name, cn) if schema and name else None
            p = col_prof.get(ck, {}) if ck else {}
            evidence_parts = []
            if p.get("null_pct") is not None:
                evidence_parts.append(f"null%={p['null_pct']:.2%}")
            if p.get("approx_distinct") is not None:
                evidence_parts.append(f"distinct≈{p['approx_distinct']}")
            if p.get("min_max"):
                evidence_parts.append(f"min={p['min_max'].get('min')} max={p['min_max'].get('max')}")
            if p.get("top_values"):
                tv = p["top_values"][:3]
                evidence_parts.append("top=" + ", ".join([f"{t['value']}({t['cnt']})" for t in tv]))
            evidence = "; ".join(evidence_parts)
            md_lines.append(f"| `{cn}` | {dt} | {nullable} | {desc} | {evidence} |\n")

    return {
        "final_markdown": "".join(md_lines),
        "final_json": out_json,
    }


# ----------------------------
# Build the graph
# ----------------------------

def build_graph():
    g = StateGraph(DDState)

    g.add_node("bootstrap", n_bootstrap)

    # Tool-using discovery
    g.add_node("discover_catalog_llm", n_discover_catalog_llm)
    g.add_node("tools", tool_node)
    g.add_node("extract_catalog", n_extract_catalog)

    # Object metadata fanout
    g.add_node("fanout_object_metadata", n_fanout_object_metadata)
    g.add_node("fetch_object_metadata_llm", n_fetch_object_metadata_llm)
    g.add_node("store_object_metadata", n_store_object_metadata)
    g.add_node("after_object_metadata_fetched", n_after_object_metadata_fetched)

    # Table profile fanout
    g.add_node("fanout_table_profiles", n_fanout_table_profiles)
    g.add_node("profile_table_llm", n_profile_table_llm)
    g.add_node("store_table_profile", n_store_table_profile)
    g.add_node("after_table_profiles_fetched", n_after_table_profiles_fetched)

    # Column profile fanout
    g.add_node("fanout_column_profiles", n_fanout_column_profiles)
    g.add_node("profile_column_llm", n_profile_column_llm)
    g.add_node("store_column_profile", n_store_column_profile)
    g.add_node("after_column_profiles_fetched", n_after_column_profiles_fetched)

    # LLM doc + join verify + QA + render
    g.add_node("write_table_docs", n_write_table_docs)

    g.add_node("fanout_join_verifications", n_fanout_join_verifications)
    g.add_node("verify_join_llm", n_verify_join_llm)
    g.add_node("store_join_verification", n_store_join_verification)
    g.add_node("after_join_verifications", n_after_join_verifications)

    g.add_node("qa_pass", n_qa_pass)
    g.add_node("render_outputs", n_render_outputs)

    # Edges
    g.add_edge(START, "bootstrap")
    g.add_edge("bootstrap", "discover_catalog_llm")

    # Standard LLM->tools loop
    g.add_conditional_edges("discover_catalog_llm", tools_condition, {"tools": "tools", "__end__": "extract_catalog"})
    g.add_edge("tools", "discover_catalog_llm")  # return to LLM after tools execute
    g.add_edge("extract_catalog", "dispatch_object_metadata")

    # Dispatch nodes implemented as "routers" returning Send
    g.add_node("dispatch_object_metadata", n_dispatch_object_metadata)
    g.add_edge("dispatch_object_metadata", "fanout_object_metadata")
    g.add_edge("after_object_metadata_fetched", "dispatch_table_profiles")

    # Object metadata loop: LLM -> tools -> store
    g.add_conditional_edges("fetch_object_metadata_llm", tools_condition, {"tools": "tools", "__end__": "store_object_metadata"})
    g.add_edge("tools", "fetch_object_metadata_llm")
    g.add_edge("store_object_metadata", "dispatch_object_metadata")  # continue fanout until done

    # Table profiling dispatch
    g.add_node("dispatch_table_profiles", n_dispatch_table_profiles)
    g.add_edge("dispatch_table_profiles", "fanout_table_profiles")

    g.add_conditional_edges("profile_table_llm", tools_condition, {"tools": "tools", "__end__": "store_table_profile"})
    g.add_edge("tools", "profile_table_llm")
    g.add_edge("store_table_profile", "dispatch_table_profiles")
    g.add_edge("after_table_profiles_fetched", "dispatch_column_profiles")

    # Column profiling dispatch
    g.add_node("dispatch_column_profiles", n_dispatch_column_profiles)
    g.add_edge("dispatch_column_profiles", "fanout_column_profiles")

    g.add_conditional_edges("profile_column_llm", tools_condition, {"tools": "tools", "__end__": "store_column_profile"})
    g.add_edge("tools", "profile_column_llm")
    g.add_edge("store_column_profile", "dispatch_column_profiles")
    g.add_edge("after_column_profiles_fetched", "write_table_docs")

    # Join verification fanout
    g.add_edge("write_table_docs", "verify_joins_dispatch")
    g.add_node("verify_joins_dispatch", n_verify_joins_dispatch)
    g.add_edge("verify_joins_dispatch", "fanout_join_verifications")

    g.add_conditional_edges("verify_join_llm", tools_condition, {"tools": "tools", "__end__": "store_join_verification"})
    g.add_edge("tools", "verify_join_llm")
    g.add_edge("store_join_verification", "verify_joins_dispatch")
    g.add_edge("after_join_verifications", "qa_pass")

    g.add_edge("qa_pass", "render_outputs")
    g.add_edge("render_outputs", END)

    checkpointer = MemorySaver()
    return g.compile(checkpointer=checkpointer)


# ----------------------------
# Run
# ----------------------------

if __name__ == "__main__":
    graph = build_graph()

    # You can attach a thread_id to resume from checkpoints if you later add a persistent checkpointer.
    # Some LangGraph runtimes support max_concurrency in config. If yours does not, just remove it.
    initial: DDState = {"messages": []}

    result = graph.invoke(
        initial,
        config={
            "configurable": {"thread_id": "data-dict-build-1"},
            # "max_concurrency": 8,  # uncomment if your LangGraph runtime supports it
        },
    )

    # Write outputs
    md = result.get("final_markdown", "")
    out_json = result.get("final_json", {})

    with open("data_dictionary.md", "w", encoding="utf-8") as f:
        f.write(md)

    with open("data_dictionary.json", "w", encoding="utf-8") as f:
        json.dump(out_json, f, indent=2, default=str)

    print("Wrote data_dictionary.md and data_dictionary.json")
