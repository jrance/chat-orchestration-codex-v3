from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

import pyodbc
from langchain_core.tools import tool


def _quote_ident(ident: str) -> str:
    # Bracket quoting with escaping for closing bracket.
    if not ident or not isinstance(ident, str):
        raise ValueError("Identifier must be a non-empty string")
    return "[" + ident.replace("]", "]]") + "]"


def _json_safe(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        # avoid Decimal serialization issues
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        # don't emit raw bytes; summarize
        b = bytes(value)
        return {"_type": "bytes", "length": len(b)}
    # fallback
    return str(value)


def _normalize_type(t: str) -> Literal["table", "view"]:
    if not t or not isinstance(t, str):
        return "table"
    tt = t.strip().lower()
    if tt in ("table", "u"):
        return "table"
    if tt in ("view", "v"):
        return "view"
    raise ValueError("type must be 'table' or 'view'")


@tool("sql_profile_table")
def sql_profile_table(
    connection_string: str,
    schema: str,
    name: str,
    type: str = "table",
    sample_rows: int = 10,
    strict_no_sample_values: bool = False,
    compute_row_count: bool = True,
) -> Dict[str, Any]:
    """
    Lightweight profiling for a table/view:
      - row_count (optional)
      - TOP-N sample rows (optional; omitted when strict_no_sample_values=True)

    Returns:
      {
        "schema": "...",
        "name": "...",
        "type": "table"|"view",
        "row_count": int|null,
        "sample_rows": [ {col: val, ...}, ... ],
        "sample_row_count": int,
        "notes": { ... }
      }
    """
    if not connection_string or not isinstance(connection_string, str):
        raise ValueError("connection_string must be a non-empty string")
    if not schema or not isinstance(schema, str):
        raise ValueError("schema must be a non-empty string")
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string")

    obj_type = _normalize_type(type)

    if sample_rows is None:
        sample_rows = 10
    sample_rows = int(sample_rows)
    if sample_rows < 0:
        raise ValueError("sample_rows must be >= 0")

    full_name = f"{_quote_ident(schema)}.{_quote_ident(name)}"

    row_count: Optional[int] = None
    sample: List[Dict[str, Any]] = []
    notes: Dict[str, Any] = {}

    with pyodbc.connect(connection_string) as conn:
        conn.timeout = 60
        cur = conn.cursor()

        # --- Row count ---
        if compute_row_count:
            if obj_type == "table":
                # Fast, metadata-based estimate for tables (heap/clustered index)
                cur.execute(
                    """
                    SELECT CAST(SUM(p.rows) AS BIGINT) AS row_count
                    FROM sys.partitions p
                    INNER JOIN sys.objects o ON o.object_id = p.object_id
                    INNER JOIN sys.schemas s ON s.schema_id = o.schema_id
                    WHERE s.name = ?
                      AND o.name = ?
                      AND o.type = 'U'
                      AND p.index_id IN (0, 1);
                    """,
                    (schema, name),
                )
                rc = cur.fetchone()
                row_count = int(rc[0]) if rc and rc[0] is not None else None
            else:
                # Views: COUNT_BIG(*) can be expensive; caller can turn off compute_row_count.
                # We do it only because compute_row_count=True explicitly asks for it.
                notes["row_count_warning"] = "View row_count computed via COUNT_BIG(*); may be expensive."
                cur.execute(f"SELECT COUNT_BIG(1) FROM {full_name};")
                rc = cur.fetchone()
                row_count = int(rc[0]) if rc and rc[0] is not None else None

        # --- Sample rows (optional) ---
        if not strict_no_sample_values and sample_rows > 0:
            # TOP (?) cannot parameterize object identifiers, but TOP value can be a parameter in many cases.
            # Safer approach: embed TOP with validated integer.
            top_n = min(sample_rows, 1000)
            cur.execute(f"SELECT TOP ({top_n}) * FROM {full_name};")
            col_names = [d[0] for d in (cur.description or [])]

            for r in cur.fetchall():
                row_dict: Dict[str, Any] = {}
                for i, col in enumerate(col_names):
                    row_dict[col] = _json_safe(r[i])
                sample.append(row_dict)
        else:
            if strict_no_sample_values:
                notes["samples_omitted"] = "strict_no_sample_values=true"
            elif sample_rows == 0:
                notes["samples_omitted"] = "sample_rows=0"

    return {
        "schema": schema,
        "name": name,
        "type": obj_type,
        "row_count": row_count,
        "sample_rows": sample,
        "sample_row_count": len(sample),
        "notes": notes,
    }
