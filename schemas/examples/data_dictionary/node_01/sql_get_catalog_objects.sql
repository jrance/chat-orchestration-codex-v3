from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

import pyodbc
from langchain_core.tools import tool


def _normalize_object_types(include_object_types: Optional[Sequence[str]]) -> List[str]:
    """
    Map user-friendly types to SQL Server sys.objects types:
      - table -> 'U'
      - view  -> 'V'
    """
    if not include_object_types:
        include_object_types = ["table", "view"]

    mapping = {
        "table": "U",
        "view": "V",
    }

    sql_types: List[str] = []
    for t in include_object_types:
        if not isinstance(t, str):
            continue
        key = t.strip().lower()
        if key in mapping and mapping[key] not in sql_types:
            sql_types.append(mapping[key])

    # Default if user passes nothing valid
    return sql_types or ["U", "V"]


def _placeholders(n: int) -> str:
    # pyodbc uses '?' placeholders
    return ",".join(["?"] * n)


@tool("sql_get_catalog_objects")
def sql_get_catalog_objects(
    connection_string: str,
    include_schemas: Optional[List[str]] = None,
    exclude_schemas: Optional[List[str]] = None,
    include_object_types: Optional[List[str]] = None,
    include_system_schemas: bool = False,
) -> List[Dict[str, Any]]:
    """
    Return a list of tables/views in the database.

    Output shape (per item):
      {
        "schema": "dbo",
        "name": "Orders",
        "type": "table" | "view"
      }

    Notes:
    - Filters are applied in this order:
        1) object types (table/view)
        2) include_schemas (if provided and non-empty)
        3) exclude_schemas (if provided and non-empty)
    - By default, excludes system schemas and ms_shipped objects.
    """
    if not connection_string or not isinstance(connection_string, str):
        raise ValueError("connection_string must be a non-empty string")

    include_schemas = include_schemas or []
    exclude_schemas = exclude_schemas or []

    sql_types = _normalize_object_types(include_object_types)

    where_clauses: List[str] = []
    params: List[Any] = []

    # Object types
    where_clauses.append(f"o.type IN ({_placeholders(len(sql_types))})")
    params.extend(sql_types)

    # System objects/schemas filtering
    if not include_system_schemas:
        where_clauses.append("o.is_ms_shipped = 0")
        where_clauses.append("s.name NOT IN ('sys', 'INFORMATION_SCHEMA')")

    # Schema include/exclude filters
    if include_schemas:
        where_clauses.append(f"s.name IN ({_placeholders(len(include_schemas))})")
        params.extend(include_schemas)

    if exclude_schemas:
        where_clauses.append(f"s.name NOT IN ({_placeholders(len(exclude_schemas))})")
        params.extend(exclude_schemas)

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
      AND o.type IN ('U','V') -- safety guard: only tables/views
    ORDER BY s.name, o.name;
    """

    # Execute
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
