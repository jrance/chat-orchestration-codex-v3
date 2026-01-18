from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional

import pyodbc
from langchain_core.tools import tool


# -----------------------------
# Helpers
# -----------------------------

def _quote_ident(ident: str) -> str:
    """Bracket-quote an identifier and escape ']'."""
    if not ident or not isinstance(ident, str):
        raise ValueError("Identifier must be a non-empty string")
    return "[" + ident.replace("]", "]]") + "]"


def _json_safe(value: Any) -> Any:
    """Make values JSON-friendly and avoid huge/binary payloads."""
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
        # avoid embedding raw bytes in JSON
        return {"_type": "bytes", "length": len(b)}
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


# -----------------------------
# Tool
# -----------------------------

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

    Fix for ODBC Driver 17 + pyodbc HY106 on datetimeoffset:
      - Never SELECT * for samples; instead build a select-list and CAST problematic types
        (datetimeoffset, sql_variant, xml, geography/geometry/hierarchyid, etc.) to NVARCHAR.

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
        # Optional: small tweaks that sometimes help with driver behavior
        # (Leaving them default-safe)
        conn.timeout = 60
        cur = conn.cursor()

        # --- Row count ---
        if compute_row_count:
            if obj_type == "table":
                # Fast metadata-based count for tables (heap/clustered index)
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
                # Views: expensive; caller can disable
                notes["row_count_warning"] = "View row_count computed via COUNT_BIG(*); may be expensive."
                cur.execute(f"SELECT COUNT_BIG(1) FROM {full_name};")
                rc = cur.fetchone()
                row_count = int(rc[0]) if rc and rc[0] is not None else None

        # --- Sample rows (safe select-list; avoids unsupported types like datetimeoffset) ---
        if strict_no_sample_values:
            notes["samples_omitted"] = "strict_no_sample_values=true"
        elif sample_rows == 0:
            notes["samples_omitted"] = "sample_rows=0"
        else:
            top_n = min(sample_rows, 1000)

            # Get columns + types in ordinal order
            cur.execute(
                """
                SELECT c.column_id, c.name, t.name AS data_type
                FROM sys.columns c
                INNER JOIN sys.objects o ON o.object_id = c.object_id
                INNER JOIN sys.schemas s ON s.schema_id = o.schema_id
                INNER JOIN sys.types t ON t.user_type_id = c.user_type_id
                WHERE s.name = ?
                  AND o.name = ?
                  AND o.type IN ('U','V')
                ORDER BY c.column_id;
                """,
                (schema, name),
            )
            cols = cur.fetchall()

            if not cols:
                notes["samples_omitted"] = "No columns found for object"
            else:
                casted_cols: List[str] = []
                select_exprs: List[str] = []
                col_names: List[str] = []

                for _, col_name, data_type in cols:
                    col_name = str(col_name)
                    dt = (str(data_type) if data_type is not None else "").lower()
                    qcol = _quote_ident(col_name)

                    # Cast types that commonly break pyodbc fetch with ODBC Driver 17
                    # datetimeoffset is ODBC type -155 (your error)
                    if dt == "datetimeoffset":
                        expr = f"CONVERT(nvarchar(50), {qcol}, 127) AS {qcol}"
                        casted_cols.append(col_name)
                    elif dt in ("sql_variant", "xml", "hierarchyid", "geography", "geometry"):
                        expr = f"CONVERT(nvarchar(4000), {qcol}) AS {qcol}"
                        casted_cols.append(col_name)
                    elif dt in ("image", "varbinary", "binary"):
                        # Convert binary to hex string to avoid binary payloads/driver decoding issues
                        expr = f"CONVERT(nvarchar(4000), sys.fn_varbintohexstr({qcol})) AS {qcol}"
                        casted_cols.append(col_name)
                    elif dt in ("text", "ntext"):
                        # legacy text types
                        expr = f"CONVERT(nvarchar(4000), {qcol}) AS {qcol}"
                        casted_cols.append(col_name)
                    else:
                        expr = f"{qcol}"

                    select_exprs.append(expr)
                    col_names.append(col_name)

                if casted_cols:
                    notes["sample_casted_columns"] = casted_cols

                sample_sql = f"SELECT TOP ({top_n}) {', '.join(select_exprs)} FROM {full_name};"
                cur.execute(sample_sql)

                for r in cur.fetchall():
                    row_dict: Dict[str, Any] = {}
                    for i, cn in enumerate(col_names):
                        row_dict[cn] = _json_safe(r[i])
                    sample.append(row_dict)

    return {
        "schema": schema,
        "name": name,
        "type": obj_type,
        "row_count": row_count,
        "sample_rows": sample,
        "sample_row_count": len(sample),
        "notes": notes,
    }
