from __future__ import annotations

from typing import Any, Dict, List, Optional

import pyodbc
from langchain_core.tools import tool


def _parse_csv_list(value: Optional[str]) -> List[str]:
    """
    Parse a comma-separated list into a de-duplicated list of trimmed tokens.
    Empty/None -> [].
    """
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    # drop empties and dedupe while preserving order
    seen = set()
    out: List[str] = []
    for p in parts:
        if not p:
            continue
        key = p.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def _placeholders(n: int) -> str:
    # pyodbc uses '?' placeholders
    return ",".join(["?"] * n)


@tool("sql_get_catalog_objects")
def sql_get_catalog_objects(
    connection_string: str,
    include_schemas: str = "",
    include_tables: bool = True,
    include_views: bool = True,
) -> List[Dict[str, Any]]:
    """
    List SQL Server catalog objects (tables/views) with optional schema filters.

    Parameters
    ----------
    connection_string:
        SQL Server connection string to the database.
    include_schemas:
        Comma-separated list of schema names. If non-empty, only objects in these schemas are returned.
    include_tables:
        Whether to include user tables (sys.objects.type = 'U').
    include_views:
        Whether to include views (sys.objects.type = 'V').

    Returns
    -------
    List[Dict[str, Any]]:
        Each item:
          {
            "schema": "dbo",
            "name": "Orders",
            "type": "table" | "view"
          }

    Notes
    -----
    - Excludes system schemas (sys, INFORMATION_SCHEMA) and ms_shipped objects.
    - If include_tables and include_views are both False, returns an empty list.
    """
    if not connection_string or not isinstance(connection_string, str):
        raise ValueError("connection_string must be a non-empty string")

    # Determine object types to include
    sql_types: List[str] = []
    if include_tables:
        sql_types.append("U")
    if include_views:
        sql_types.append("V")
    if not sql_types:
        return []

    schemas = _parse_csv_list(include_schemas)

    where_clauses: List[str] = []
    params: List[Any] = []

    # Object types
    where_clauses.append(f"o.type IN ({_placeholders(len(sql_types))})")
    params.extend(sql_types)

    # Default exclusions for quality/safety
    where_clauses.append("o.is_ms_shipped = 0")
    where_clauses.append("s.name NOT IN ('sys', 'INFORMATION_SCHEMA')")

    # Optional schema allow-list
    if schemas:
        where_clauses.append(f"s.name IN ({_placeholders(len(schemas))})")
        params.extend(schemas)

    where_sql = " AND ".join(where_clauses) if where_clauses else "1=1"

    query = f"""
    SELECT
        s.name AS schema_name,
        o.name AS object_name,
        CASE
            WHEN o.type = 'U' THEN 'table'
            WHEN o.type = 'V' THEN 'view'
            ELSE o.type
        END AS object_type
    FROM sys.objects o
    INNER JOIN sys.schemas s
        ON o.schema_id = s.schema_id
    WHERE {where_sql}
      AND o.type IN ('U','V') -- safety guard
    ORDER BY s.name, o.name;
    """

    cn = pyodbc.connect(connection_string)
    try:
        cur = cn.cursor()
        cur.execute(query, params)
        rows = cur.fetchall()

        results: List[Dict[str, Any]] = []
        for r in rows:
            results.append(
                {
                    "schema": str(r.schema_name),
                    "name": str(r.object_name),
                    "type": str(r.object_type),
                }
            )
        return results
    finally:
        try:
            cn.close()
        except Exception:
            pass
