from __future__ import annotations
from typing import Any, Dict, List, Sequence, Optional

def unique_strings_from_columns_simple(
    rows: List[Dict[str, Any]],
    columns: Sequence[str],
    *,
    case_insensitive: bool = False,
    strip: bool = True,
    drop_empty: bool = True,
    split: Optional[str] = None,
) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()

    for row in rows:
        for col in columns:
            cell = row.get(col)
            if cell is None:
                continue

            values = cell if isinstance(cell, (list, tuple, set)) else [cell]

            for v in values:
                if v is None:
                    continue

                if isinstance(v, str):
                    parts = v.split(split) if split else [v]
                else:
                    parts = [str(v)]

                for p in parts:
                    s = p.strip() if strip else p
                    if drop_empty and not s:
                        continue
                    key = s.casefold() if case_insensitive else s
                    if key not in seen:
                        seen.add(key)
                        out.append(s)

    return out
