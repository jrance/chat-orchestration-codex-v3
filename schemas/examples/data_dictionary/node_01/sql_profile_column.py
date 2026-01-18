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


def _is_numeric_type(data_type: str) -> bool:
    t = (data_type or "").lower()
    return any(x in t for x in ("int", "decimal", "numeric", "money", "float", "real", "bigint", "smallint", "tinyint"))


def _is_datetime_type(data_type: str) -> bool:
    t = (data_type or "").lower()
    return any(x in t for x in ("date", "time", "datetime", "smalldatetime", "datetime2", "datetimeoffset"))


def _is_text_type(data_type: str) -> bool:
    t = (data_type or "").lower()
    return any(x in t for x in ("char", "varchar", "nchar", "nvarchar", "text", "ntext"))


def _needs_cast_for_pyodbc(data_type: str) -> bool:
    """
    Types that often cause pyodbc decode errors (e.g., datetimeoffset => ODBC -155) or
    otherwise are better treated as strings for profiling outputs.
    """
    t = (data_type or "").lower()
    return t in ("datetimeoffset", "sql_variant", "xml", "hierarchyid", "geography", "geometry", "image", "varbinary", "binary", "text", "ntext")


def _cast_expr(qcol: str, data_type: str) -> str:
    """
    Return SQL expression that safely converts the column to NVARCHAR for retrieval.
    """
    t = (data_type or "").lower()
    if t == "datetimeoffset":
        # ISO 8601-ish (style 127)
        return f"CONVERT(nvarchar(50), {qcol}, 127)"
    if t in ("hierarchyid", "geography", "geometry", "sql_variant", "xml", "text", "ntext"):
        return f"CONVERT(nvarchar(4000), {qcol})"
    if t in ("image", "varbinary", "binary"):
        return f"CONVERT(nvarchar(4000), sys.fn_varbintohexstr({qcol}))"
    # fallback
    return f"CONVERT(nvarchar(4000), {qcol})"


# -----------------------------
# Tool
# -----------------------------

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
      - distinct_count
      - min/max for numeric/datetime (when safe)
      - max_length for text (when safe)
      - top values + sample distinct values (optional)

    Includes fix for ODBC Driver 17 + pyodbc HY106 on datetimeoffset (-155):
      - casts problematic types to NVARCHAR in queries that return the value.
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
    qcol = _quote_ident(column_name)

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

        # 1) Get column SQL type from sys catalog
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

        # Determine whether to cast value-returning queries
        needs_cast = _needs_cast_for_pyodbc(data_type)
        value_expr = _cast_expr(qcol, data_type) if needs_cast else qcol
        if needs_cast:
            notes["value_cast"] = f"{data_type} -> nvarchar"
            # When we cast, min/max on original type may still be OK for numeric/datetimeoffset? datetimeoffset is problematic
            # We'll keep min/max only if truly safe.
        safe_for_minmax = _is_numeric_type(data_type) or (_is_datetime_type(data_type) and data_type.lower() != "datetimeoffset")

        # 2) Row count
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

        # 3) Aggregates (null_count, distinct_count, min/max/max_len)
        agg_parts: List[str] = [
            f"SUM(CASE WHEN {qcol} IS NULL THEN 1 ELSE 0 END) AS null_count",
            f"COUNT_BIG(DISTINCT {qcol}) AS distinct_count",
        ]

        if safe_for_minmax:
            agg_parts.append(f"MIN({qcol}) AS min_val")
            agg_parts.append(f"MAX({qcol}) AS max_val")
        elif _is_text_type(data_type) and not needs_cast:
            # If it's text-ish but not requiring cast, max length is meaningful
            agg_parts.append(f"MAX(LEN({qcol})) AS max_len")

        agg_sql = f"SELECT {', '.join(agg_parts)} FROM {full_table};"
        cur.execute(agg_sql)
        agg = cur.fetchone()

        null_count = int(agg[0]) if agg and agg[0] is not None else None
        distinct_count = int(agg[1]) if agg and agg[1] is not None else None

        if safe_for_minmax and agg and len(agg) >= 4:
            min_val = _json_safe(agg[2], max_len=max_value_length)
            max_val = _json_safe(agg[3], max_len=max_value_length)
        elif _is_text_type(data_type) and not needs_cast and agg and len(agg) >= 3:
            max_len = int(agg[2]) if agg[2] is not None else None

        null_pct: Optional[float] = None
        if row_count and null_count is not None and row_count > 0:
            null_pct = float(null_count) / float(row_count)

        # 4) Top values (value-returning query => use value_expr when needed)
        if not strict_no_sample_values and top_values > 0:
            top_n = min(top_values, 200)
            # Grouping: if we cast, group by the casted expression to avoid unsupported fetch on raw type.
            top_sql = f"""
            SELECT TOP ({top_n})
                {value_expr} AS value,
                COUNT_BIG(1) AS cnt
            FROM {full_table}
            WHERE {qcol} IS NOT NULL
            GROUP BY {value_expr}
            ORDER BY COUNT_BIG(1) DESC;
            """
            cur.execute(top_sql)
            for v, cnt in cur.fetchall():
                pct = (float(cnt) / float(row_count)) if row_count and row_count > 0 else None
                top_vals_out.append(
                    {
                        "value": _json_safe(v, max_len=max_value_length),
                        "count": int(cnt),
                        "pct": pct,
                    }
                )

        # 5) Sample distinct values (value-returning query => use value_expr when needed)
        if not strict_no_sample_values and sample_values > 0:
            n = min(sample_values, 200)
            sample_sql = f"""
            SELECT TOP ({n}) v.value
            FROM (
              SELECT DISTINCT {value_expr} AS value
              FROM {full_table}
              WHERE {qcol} IS NOT NULL
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
