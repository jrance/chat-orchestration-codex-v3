from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from langchain_core.tools import tool


def _parse_csv(value: Optional[str]) -> List[str]:
    if not value:
        return []
    parts = [p.strip() for p in value.split(",")]
    out: List[str] = []
    seen: Set[str] = set()
    for p in parts:
        if not p:
            continue
        k = p.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def _col_name_score(name: str) -> int:
    """
    Heuristic score purely from column name patterns.
    Higher => more likely to be important for profiling/meaning.
    """
    n = (name or "").strip().lower()
    if not n:
        return 0

    score = 0

    # identifiers / keys
    if n == "id" or n.endswith("_id") or n.endswith("id"):
        score += 80
    if "key" in n:
        score += 40

    # enums/flags
    if "status" in n:
        score += 60
    if "type" in n:
        score += 55
    if "code" in n:
        score += 50
    if n.startswith("is_") or n.startswith("has_") or n.endswith("_flag") or "flag" in n:
        score += 45

    # dates/times
    if "date" in n or "time" in n or n.endswith("_at"):
        score += 45
    if "created" in n or "updated" in n or "modified" in n:
        score += 55

    # amounts/metrics
    if "amount" in n or "total" in n or "price" in n or "cost" in n:
        score += 40
    if "qty" in n or "quantity" in n or "count" in n or "num_" in n:
        score += 35
    if "rate" in n or "pct" in n or "percent" in n:
        score += 25

    # human-readable strings
    if "name" in n or "title" in n or "desc" in n or "description" in n:
        score += 30
    if "email" in n or "phone" in n:
        score += 25
    if "address" in n or "city" in n or "state" in n or "zip" in n or "postal" in n or "country" in n:
        score += 20

    return score


def _type_score(data_type: Optional[str]) -> int:
    """
    Small bump to prioritize columns that are informative when profiled.
    """
    t = (data_type or "").lower()
    if not t:
        return 0
    if t in ("bit",):
        return 10
    if "date" in t or "time" in t:
        return 15
    if t in ("uniqueidentifier",):
        return 5
    if any(x in t for x in ("char", "text", "nchar", "nvarchar", "varchar")):
        return 12
    if any(x in t for x in ("int", "decimal", "numeric", "money", "float", "real", "bigint", "smallint", "tinyint")):
        return 10
    return 0


def _extract_pk_fk(object_metadata: Dict[str, Any]) -> Tuple[Set[str], Set[str]]:
    pk: Set[str] = set()
    fk: Set[str] = set()

    primary_key = object_metadata.get("primary_key")
    if isinstance(primary_key, dict):
        cols = primary_key.get("columns")
        if isinstance(cols, list):
            pk = {str(c) for c in cols if c is not None}

    foreign_keys = object_metadata.get("foreign_keys")
    if isinstance(foreign_keys, list):
        for fk_obj in foreign_keys:
            if not isinstance(fk_obj, dict):
                continue
            # supports either shape:
            #  - {"from": {"columns": [...]}, "to": {...}}
            #  - {"columns": [...], "referenced_columns": [...]}
            from_part = fk_obj.get("from")
            if isinstance(from_part, dict):
                cols = from_part.get("columns")
                if isinstance(cols, list):
                    fk.update({str(c) for c in cols if c is not None})
            cols = fk_obj.get("columns")
            if isinstance(cols, list):
                fk.update({str(c) for c in cols if c is not None})

    # normalize lower-case comparison sets, but keep original names later
    pk_l = {c.lower() for c in pk}
    fk_l = {c.lower() for c in fk}
    return pk_l, fk_l


@tool("columns_to_profile")
def columns_to_profile(
    object_metadata: Dict[str, Any],
    max_columns_profiled_per_object: int = 80,
    include_columns: str = "",
    exclude_columns: str = "",
    return_full_metadata: bool = False,
) -> Any:
    """
    Deterministically select which columns to profile for an object.

    Strategy:
      1) If total columns <= max, return all (minus exclude).
      2) Always include: PK/FK columns + explicitly included columns + columns with existing MS_Description.
      3) Fill remaining budget using name/type heuristics.

    Returns:
      - If return_full_metadata=False: List[str] of column names
      - If return_full_metadata=True: List[Dict] column objects from object_metadata["columns"]
    """
    if not isinstance(object_metadata, dict):
        raise ValueError("object_metadata must be a JSON object/dict")

    cols = object_metadata.get("columns")
    if not isinstance(cols, list):
        raise ValueError("object_metadata.columns must be a list")

    max_n = int(max_columns_profiled_per_object)
    if max_n < 1:
        raise ValueError("max_columns_profiled_per_object must be >= 1")

    include_list = _parse_csv(include_columns)
    exclude_list = {c.lower() for c in _parse_csv(exclude_columns)}

    # Map colname -> colobj (preserve original casing)
    by_name: Dict[str, Dict[str, Any]] = {}
    ordered_names: List[str] = []
    for c in cols:
        if not isinstance(c, dict):
            continue
        n = c.get("name")
        if not n:
            continue
        n_str = str(n)
        key = n_str.lower()
        if key in by_name:
            continue
        by_name[key] = c
        ordered_names.append(n_str)

    total_cols = len(by_name)

    # Fast path: small enough, return all (minus excludes)
    if total_cols <= max_n:
        selected_keys = [k for k in by_name.keys() if k not in exclude_list]
        # Keep original order from sys.columns
        selected_ordered = [n for n in ordered_names if n.lower() in set(selected_keys)]
        if return_full_metadata:
            return [by_name[n.lower()] for n in selected_ordered]
        return selected_ordered

    pk_l, fk_l = _extract_pk_fk(object_metadata)

    selected: Set[str] = set()

    # Always include PK/FK
    selected.update(pk_l)
    selected.update(fk_l)

    # Always include columns that already have a description (MS_Description)
    for k, c in by_name.items():
        if k in exclude_list:
            continue
        if c.get("description"):
            selected.add(k)

    # Always include explicit include_columns
    for col in include_list:
        k = col.lower()
        if k in by_name and k not in exclude_list:
            selected.add(k)

    # Score remaining columns
    scored: List[Tuple[int, str]] = []
    for k, c in by_name.items():
        if k in exclude_list or k in selected:
            continue
        name = str(c.get("name") or "")
        dt = c.get("data_type")
        score = _col_name_score(name) + _type_score(dt)
        scored.append((score, k))

    # Sort high-to-low, stable tie-breaker by name
    scored.sort(key=lambda x: (-x[0], x[1]))

    # Fill up to max_n
    for _, k in scored:
        if len(selected) >= max_n:
            break
        selected.add(k)

    # Return in original column order
    selected_lower = selected
    selected_ordered = [n for n in ordered_names if n.lower() in selected_lower and n.lower() not in exclude_list]

    if return_full_metadata:
        return [by_name[n.lower()] for n in selected_ordered]
    return selected_ordered
