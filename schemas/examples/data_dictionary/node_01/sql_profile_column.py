from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, List, Literal, Optional, Tuple

import pyodbc
from langchain_core.tools import tool


def _quote_ident(ident: str) -> str:
    if not ident or not isinstance(ident, str):
        raise ValueError("Identifier must be a non-empty string")
    return "[" + ident.replace("]", "]]") + "]"


def _json_safe(value: Any, *, max_len: int) -> Any:
    if value is None:
        return None
    if isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
        return {"_type": "bytes", "length": len(b)}
    s = str(value)
    if max_len and len(s) > max_len:
        return s[:max_len] + "…"
    return s


def _normalize_table_type(t: str) -> Literal["table", "view"]:
    if not t:
        return "table"
    tt = t.strip().lower()
    if tt in ("table", "u"):
        return "table"
    if tt in ("view", "v"):
        return "view"
    raise ValueError("table_type must be 'table' or 'view'")


def _infer_is_numeric(data_type: str) -> bool:
    t = (data_type or "").lower()
    return any(x in t for x in ["int", "decimal", "numeric", "money", "float", "real", "bigint", "smallint", "tinyint"])


def _infer_is_datetime(data_type: str) -> bool:
    t = (data_type or "").lower()
    return any(x in t for x in ["date", "time", "datetime", "smalldatetime", "datetime2", "datetimeoffset"])


def _infer_is_text(data_type: str) -> bool:
    t = (data_type or "").lower()
    return any(x in t for x in ["char", "varchar", "nchar", "nvarchar", "text", "ntext"])


@tool("sql_profile_column")
def sql_profile_column(
    connection_string: str,
    schema: str,
    table_name: str,
    table_type: str = "table",
    column_name: str = "",
    top_values: int = 10,
    sample_values: int = 10,
    strict_no_sample_values: bool = False,
    max_value_length: int = 200,
) -> Dict[str, Any]:
    """
    Profile a single column:
      - row_count (best-effort)
      - null_count / null_pct
      - distinct_count (exact for <= ~2M rows; otherwise approximate via COUNT_BIG(DISTINCT))
      - min/max for numeric/datetime
      - max_length for text
      - top values (optional)
      - sample distinct values (optional)

    Returns:
      {
        "schema": "...",
        "table_name": "...",
        "table_type": "table"|"view",
        "column_name": "...",
        "data_type": "...",
        "row_count": int|null,
        "null_count": int|null,
        "null_pct": float|null,
        "distinct_count": int|null,
        "min": any|null,
        "max": any|null,
        "max_length": int|null,
        "top_values": [{"value":..., "count":..., "pct":...}, ...],
        "sample_values": [...],
        "notes": {...}
      }
    """
    if not connection_string or not isinstance(connection_string, str):
        raise ValueError("connection_string must be a non-empty string")
    if not schema or not isinstance(schema, str):
        raise ValueError("schema must be a non-empty string")
    if not table_name or not isinstance(table_name, str):
        raise ValueError("table_name must be a non-empty string")
    if not column_name or not isinstance(column_name, str):
        raise ValueError("column_name must be a non-empty string")

    tt = _normalize_table_type(table_type)

    top_values = int(top_values) if top_values is not None else 10
    sample_values = int(sample_values) if sample_values is not None else 10
    max_value_length = int(max_value_length) if max_value_length is not None else 200

    if top_values < 0 or sample_values < 0:
        raise ValueError("top_values and sample_values must be >= 0")

    full_table = f"{_quote_ident(schema)}.{_quote_ident(table_name)}"
    col_ident = _quote_ident(column_name)

    notes: Dict[str, Any] = {}
    row_count: Optional[int] = None
    null_count: Optional[int] = None
    distinct_count: Optional[int] = None
    min_val: Any = None
    max_val: Any = None
    max_len: Optional[int] = None
    top_vals_out: List[Dict[str, Any]] = []
    sample_vals_out: List[Any] = []

    with pyodbc.connect(connection_string) as conn:
        conn.timeout = 120
        cur = conn.cursor()

        # 1) Get data type from sys.columns/sys.types (works for tables/views)
        cur.execute(
            """
            SELECT TOP 1 t.name AS data_type
            FROM sys.columns c
            INNER JOIN sys.objects o ON o.object_id = c.object_id
            INNER JOIN sys.schemas s ON s.schema_id = o.schema_id
            INNER JOIN sys.types t ON t.user_type_id = c.user_type_id
            WHERE s.name = ? AND o.name = ? AND c.name = ? AND o.type IN ('U','V');
            """,
            (schema, table_name, column_name),
        )
        dt_row = cur.fetchone()
        if not dt_row:
            raise ValueError(f"Column not found: {schema}.{table_name}.{column_name}")
        data_type = str(dt_row[0])

        # 2) Row count (fast for tables; potentially expensive for views)
        if tt == "table":
            cur.execute(
                """
                SELECT CAST(SUM(p.rows) AS BIGINT)
                FROM sys.partitions p
                INNER JOIN sys.objects o ON o.object_id = p.object_id
                INNER JOIN sys.schemas s ON s.schema_id = o.schema_id
                WHERE s.name = ? AND o.name = ? AND o.type = 'U' AND p.index_id IN (0,1);
                """,
                (schema, table_name),
            )
            rc = cur.fetchone()
            row_count = int(rc[0]) if rc and rc[0] is not None else None
        else:
            notes["row_count_warning"] = "View row_count computed via COUNT_BIG(*); may be expensive."
            cur.execute(f"SELECT COUNT_BIG(1) FROM {full_table};")
            rc = cur.fetchone()
            row_count = int(rc[0]) if rc and rc[0] is not None else None

        # 3) Null count, distinct count, min/max/max_length
        # Build a single aggregation query with safe quoted identifiers.
        agg_parts: List[str] = [
            f"SUM(CASE WHEN {col_ident} IS NULL THEN 1 ELSE 0 END) AS null_count",
            f"COUNT_BIG(DISTINCT {col_ident}) AS distinct_count",
        ]

        if _infer_is_numeric(data_type) or _infer_is_datetime(data_type):
            agg_parts.append(f"MIN({col_ident}) AS min_val")
            agg_parts.append(f"MAX({col_ident}) AS max_val")
        elif _infer_is_text(data_type):
            # For text-like columns, max_length helps choose description phrasing
            agg_parts.append(f"MAX(LEN({col_ident})) AS max_len")

        agg_sql = f"SELECT {', '.join(agg_parts)} FROM {full_table};"
        cur.execute(agg_sql)
        agg = cur.fetchone()

        # Map aggregation outputs by position
        null_count = int(agg[0]) if agg and agg[0] is not None else None
        distinct_count = int(agg[1]) if agg and agg[1] is not None else None

        # min/max or max_len depending on type
        if agg and len(agg) >= 4 and (_infer_is_numeric(data_type) or _infer_is_datetime(data_type)):
            min_val = _json_safe(agg[2], max_len=max_value_length)
            max_val = _json_safe(agg[3], max_len=max_value_length)
        elif agg and len(agg) >= 3 and _infer_is_text(data_type):
            max_len = int(agg[2]) if agg[2] is not None else None

        null_pct: Optional[float] = None
        if row_count and null_count is not None and row_count > 0:
            null_pct = float(null_count) / float(row_count)

        # 4) Top values (optional, omit if strict_no_sample_values)
        if not strict_no_sample_values and top_values > 0:
            # Note: For large tables/views this can be expensive.
            # You can add sampling or WHERE filters later if needed.
            top_n = min(top_values, 200)
            top_sql = f"""
            SELECT TOP ({top_n})
                {col_ident} AS value,
                COUNT_BIG(1) AS cnt
            FROM {full_table}
            WHERE {col_ident} IS NOT NULL
            GROUP BY {col_ident}
            ORDER BY COUNT_BIG(1) DESC;
            """
            cur.execute(top_sql)
            rows = cur.fetchall()
            for v, cnt in rows:
                pct = (float(cnt) / float(row_count)) if row_count and row_count > 0 else None
                top_vals_out.append(
                    {
                        "value": _json_safe(v, max_len=max_value_length),
                        "count": int(cnt),
                        "pct": pct,
                    }
                )

        # 5) Sample distinct values (optional, omit if strict_no_sample_values)
        if not strict_no_sample_values and sample_values > 0:
            n = min(sample_values, 200)
            # Prefer distinct sample; for some types it may still be heavy.
            sample_sql = f"""
            SELECT TOP ({n}) v.value
            FROM (
              SELECT DISTINCT {col_ident} AS value
              FROM {full_table}
              WHERE {col_ident} IS NOT NULL
            ) v
            ORDER BY v.value;
            """
            cur.execute(sample_sql)
            for (v,) in cur.fetchall():
                sample_vals_out.append(_json_safe(v, max_len=max_value_length))

        if strict_no_sample_values:
            notes["samples_omitted"] = "strict_no_sample_values=true"
        if top_values == 0:
            notes["top_values_omitted"] = "top_values=0"
        if sample_values == 0:
            notes["sample_values_omitted"] = "sample_values=0"

    return {
        "schema": schema,
        "table_name": table_name,
        "table_type": tt,
        "column_name": column_name,
        "data_type": data_type,
        "row_count": row_count,
        "null_count": null_count,
        "null_pct": null_pct,
        "distinct_count": distinct_count,
        "min": min_val,
        "max": max_val,
        "max_length": max_len,
        "top_values": top_vals_out,
        "sample_values": sample_vals_out,
        "notes": notes,
    }
