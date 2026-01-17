from __future__ import annotations

from typing import Any, Dict, List, Optional

import pyodbc
from langchain_core.tools import tool


def _normalize_object_type(obj_type: str) -> str:
    """
    Normalize caller-provided type into SQL Server sys.objects types.
      - "table" -> "U"
      - "view"  -> "V"
    """
    if not obj_type or not isinstance(obj_type, str):
        raise ValueError("type must be a non-empty string ('table' or 'view')")
    t = obj_type.strip().lower()
    if t in ("table", "u"):
        return "U"
    if t in ("view", "v"):
        return "V"
    raise ValueError("type must be 'table' or 'view'")


def _format_max_length(type_name: str, max_length: int) -> Optional[int]:
    """
    Convert SQL Server sys.columns.max_length into a more human-friendly length.
    For (n)char/(n)varchar, max_length is bytes; nvarchar/nchar store 2 bytes/char.
    For max types, max_length = -1.
    """
    if max_length is None:
        return None
    if max_length == -1:
        return -1  # "MAX"
    tn = (type_name or "").lower()
    if tn in ("nvarchar", "nchar"):
        return max_length // 2
    return max_length


@tool("sql_get_object_metadata")
def sql_get_object_metadata(
    connection_string: str,
    schema: str,
    name: str,
    type: str,
) -> Dict[str, Any]:
    """
    Get detailed metadata for a SQL Server table/view: columns, PK/unique keys, foreign keys,
    and extended descriptions (MS_Description) when present.

    Parameters
    ----------
    connection_string : str
        SQL Server connection string to the database.
    schema : str
        Schema name (e.g., 'dbo').
    name : str
        Object name (e.g., 'Orders').
    type : str
        Object type: 'table' or 'view'.

    Returns
    -------
    Dict[str, Any]
        {
          "schema": "dbo",
          "name": "Orders",
          "type": "table",
          "object_id": 123456,
          "columns": [
            {
              "ordinal_position": 1,
              "name": "OrderId",
              "data_type": "int",
              "max_length": null,
              "display_length": null,
              "precision": 10,
              "scale": 0,
              "is_nullable": false,
              "is_identity": true,
              "is_computed": false,
              "collation": null,
              "default_definition": null,
              "description": "..."
            },
            ...
          ],
          "primary_key": {
            "name": "PK_Orders",
            "columns": ["OrderId"]
          } | null,
          "unique_constraints": [
            {"name": "UQ_Orders_OrderNumber", "columns": ["OrderNumber"]},
            ...
          ],
          "foreign_keys": [
            {
              "name": "FK_Orders_Customers",
              "columns": ["CustomerId"],
              "referenced_schema": "dbo",
              "referenced_table": "Customers",
              "referenced_columns": ["CustomerId"],
              "update_action": "NO_ACTION",
              "delete_action": "NO_ACTION"
            },
            ...
          ],
          "referenced_by": [
            {
              "schema": "dbo",
              "name": "vw_OrderSummary",
              "type": "view"
            },
            ...
          ],
          "description": "..."  // object-level MS_Description if present, else null
        }
    """
    if not connection_string or not isinstance(connection_string, str):
        raise ValueError("connection_string must be a non-empty string")
    if not schema or not isinstance(schema, str):
        raise ValueError("schema must be a non-empty string")
    if not name or not isinstance(name, str):
        raise ValueError("name must be a non-empty string")

    obj_type = _normalize_object_type(type)

    cn = pyodbc.connect(connection_string)
    try:
        cur = cn.cursor()

        # 1) Resolve object_id and verify it exists with expected type
        cur.execute(
            """
            SELECT o.object_id, s.name AS schema_name, o.name AS object_name, o.type AS object_type
            FROM sys.objects o
            INNER JOIN sys.schemas s ON s.schema_id = o.schema_id
            WHERE s.name = ? AND o.name = ? AND o.type = ?;
            """,
            (schema, name, obj_type),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Object not found: {schema}.{name} (type={type})")

        object_id = int(row.object_id)

        # 2) Object-level description (MS_Description)
        cur.execute(
            """
            SELECT CAST(ep.value AS NVARCHAR(4000)) AS description
            FROM sys.extended_properties ep
            WHERE ep.major_id = ?
              AND ep.minor_id = 0
              AND ep.name = 'MS_Description';
            """,
            (object_id,),
        )
        obj_desc_row = cur.fetchone()
        object_description = str(obj_desc_row.description) if obj_desc_row and obj_desc_row.description is not None else None

        # 3) Column metadata + default constraints + column descriptions
        cur.execute(
            """
            SELECT
                c.column_id,
                c.name AS column_name,
                t.name AS type_name,
                c.max_length,
                c.precision,
                c.scale,
                c.is_nullable,
                c.is_identity,
                c.is_computed,
                c.collation_name,
                dc.definition AS default_definition,
                CAST(ep.value AS NVARCHAR(4000)) AS column_description
            FROM sys.columns c
            INNER JOIN sys.types t
                ON c.user_type_id = t.user_type_id
            LEFT JOIN sys.default_constraints dc
                ON c.default_object_id = dc.object_id
            LEFT JOIN sys.extended_properties ep
                ON ep.major_id = c.object_id
               AND ep.minor_id = c.column_id
               AND ep.name = 'MS_Description'
            WHERE c.object_id = ?
            ORDER BY c.column_id;
            """,
            (object_id,),
        )

        columns: List[Dict[str, Any]] = []
        for r in cur.fetchall():
            type_name = str(r.type_name)
            max_len = int(r.max_length) if r.max_length is not None else None
            columns.append(
                {
                    "ordinal_position": int(r.column_id),
                    "name": str(r.column_name),
                    "data_type": type_name,
                    "max_length": max_len if max_len is not None else None,
                    "display_length": _format_max_length(type_name, max_len) if max_len is not None else None,
                    "precision": int(r.precision) if r.precision is not None else None,
                    "scale": int(r.scale) if r.scale is not None else None,
                    "is_nullable": bool(r.is_nullable),
                    "is_identity": bool(r.is_identity),
                    "is_computed": bool(r.is_computed),
                    "collation": str(r.collation_name) if r.collation_name is not None else None,
                    "default_definition": str(r.default_definition) if r.default_definition is not None else None,
                    "description": str(r.column_description) if r.column_description is not None else None,
                }
            )

        # 4) PK + unique constraints (via indexes)
        # Primary key index
        cur.execute(
            """
            SELECT TOP 1 i.name AS index_name
            FROM sys.indexes i
            WHERE i.object_id = ?
              AND i.is_primary_key = 1;
            """,
            (object_id,),
        )
        pk_row = cur.fetchone()
        primary_key = None
        if pk_row and pk_row.index_name:
            pk_name = str(pk_row.index_name)
            cur.execute(
                """
                SELECT c.name AS column_name
                FROM sys.indexes i
                INNER JOIN sys.index_columns ic
                    ON ic.object_id = i.object_id AND ic.index_id = i.index_id
                INNER JOIN sys.columns c
                    ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                WHERE i.object_id = ?
                  AND i.is_primary_key = 1
                ORDER BY ic.key_ordinal;
                """,
                (object_id,),
            )
            pk_cols = [str(x.column_name) for x in cur.fetchall()]
            primary_key = {"name": pk_name, "columns": pk_cols}

        # Unique constraints / unique indexes (excluding PK)
        cur.execute(
            """
            SELECT i.index_id, i.name AS index_name
            FROM sys.indexes i
            WHERE i.object_id = ?
              AND i.is_unique = 1
              AND i.is_primary_key = 0
              AND i.name IS NOT NULL;
            """,
            (object_id,),
        )
        uq_indexes = cur.fetchall()
        unique_constraints: List[Dict[str, Any]] = []
        for idx in uq_indexes:
            idx_id = int(idx.index_id)
            idx_name = str(idx.index_name)
            cur.execute(
                """
                SELECT c.name AS column_name
                FROM sys.index_columns ic
                INNER JOIN sys.columns c
                    ON c.object_id = ic.object_id AND c.column_id = ic.column_id
                WHERE ic.object_id = ?
                  AND ic.index_id = ?
                  AND ic.is_included_column = 0
                ORDER BY ic.key_ordinal;
                """,
                (object_id, idx_id),
            )
            uq_cols = [str(x.column_name) for x in cur.fetchall()]
            unique_constraints.append({"name": idx_name, "columns": uq_cols})

        # 5) Foreign keys (outgoing)
        # Group rows per FK so composite keys are handled correctly
        cur.execute(
            """
            SELECT
                fk.object_id AS fk_object_id,
                fk.name AS fk_name,
                fk.update_referential_action_desc AS update_action,
                fk.delete_referential_action_desc AS delete_action,
                s_ref.name AS referenced_schema,
                o_ref.name AS referenced_table,
                c_parent.name AS parent_column,
                c_ref.name AS referenced_column,
                fkc.constraint_column_id AS ordinal_in_fk
            FROM sys.foreign_keys fk
            INNER JOIN sys.foreign_key_columns fkc
                ON fkc.constraint_object_id = fk.object_id
            INNER JOIN sys.objects o_parent
                ON o_parent.object_id = fk.parent_object_id
            INNER JOIN sys.schemas s_parent
                ON s_parent.schema_id = o_parent.schema_id
            INNER JOIN sys.objects o_ref
                ON o_ref.object_id = fk.referenced_object_id
            INNER JOIN sys.schemas s_ref
                ON s_ref.schema_id = o_ref.schema_id
            INNER JOIN sys.columns c_parent
                ON c_parent.object_id = fk.parent_object_id
               AND c_parent.column_id = fkc.parent_column_id
            INNER JOIN sys.columns c_ref
                ON c_ref.object_id = fk.referenced_object_id
               AND c_ref.column_id = fkc.referenced_column_id
            WHERE fk.parent_object_id = ?
            ORDER BY fk.object_id, fkc.constraint_column_id;
            """,
            (object_id,),
        )

        foreign_keys: List[Dict[str, Any]] = []
        fk_map: Dict[int, Dict[str, Any]] = {}
        for r in cur.fetchall():
            fk_object_id = int(r.fk_object_id)
            if fk_object_id not in fk_map:
                fk_map[fk_object_id] = {
                    "name": str(r.fk_name),
                    "columns": [],
                    "referenced_schema": str(r.referenced_schema),
                    "referenced_table": str(r.referenced_table),
                    "referenced_columns": [],
                    "update_action": str(r.update_action),
                    "delete_action": str(r.delete_action),
                }
            fk_map[fk_object_id]["columns"].append(str(r.parent_column))
            fk_map[fk_object_id]["referenced_columns"].append(str(r.referenced_column))

        foreign_keys = list(fk_map.values())

        # 6) Referenced-by (incoming FKs + view dependencies best-effort)
        # Incoming FKs
        cur.execute(
            """
            SELECT DISTINCT
                s_parent.name AS schema_name,
                o_parent.name AS object_name,
                CASE WHEN o_parent.type = 'U' THEN 'table'
                     WHEN o_parent.type = 'V' THEN 'view'
                     ELSE o_parent.type END AS object_type
            FROM sys.foreign_keys fk
            INNER JOIN sys.objects o_parent ON o_parent.object_id = fk.parent_object_id
            INNER JOIN sys.schemas s_parent ON s_parent.schema_id = o_parent.schema_id
            WHERE fk.referenced_object_id = ?
            ORDER BY s_parent.name, o_parent.name;
            """,
            (object_id,),
        )
        referenced_by: List[Dict[str, Any]] = [
            {"schema": str(r.schema_name), "name": str(r.object_name), "type": str(r.object_type)}
            for r in cur.fetchall()
        ]

        # Add view dependencies (objects that reference this object) if available (best-effort)
        # This can be noisy in some environments; keep it additive.
        try:
            cur.execute(
                """
                SELECT DISTINCT
                    s_referencing.name AS schema_name,
                    o_referencing.name AS object_name,
                    CASE WHEN o_referencing.type = 'V' THEN 'view'
                         WHEN o_referencing.type = 'U' THEN 'table'
                         ELSE o_referencing.type END AS object_type
                FROM sys.sql_expression_dependencies d
                INNER JOIN sys.objects o_referencing ON o_referencing.object_id = d.referencing_id
                INNER JOIN sys.schemas s_referencing ON s_referencing.schema_id = o_referencing.schema_id
                WHERE d.referenced_id = ?
                  AND o_referencing.type IN ('V','U')
                ORDER BY s_referencing.name, o_referencing.name;
                """,
                (object_id,),
            )
            for r in cur.fetchall():
                item = {"schema": str(r.schema_name), "name": str(r.object_name), "type": str(r.object_type)}
                if item not in referenced_by:
                    referenced_by.append(item)
        except Exception:
            # Dependency DMV may be restricted; ignore silently.
            pass

        return {
            "schema": schema,
            "name": name,
            "type": "table" if obj_type == "U" else "view",
            "object_id": object_id,
            "description": object_description,
            "columns": columns,
            "primary_key": primary_key,
            "unique_constraints": unique_constraints,
            "foreign_keys": foreign_keys,
            "referenced_by": referenced_by,
        }

    finally:
        try:
            cn.close()
        except Exception:
            pass
