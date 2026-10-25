"""Translate a metadata filter spec (see filters.py) into a parameterized SQL clause."""

from collections.abc import Mapping
from typing import Any

from app.retrieval.errors import UnsupportedFilterError
from app.retrieval.filters import Filters

_COMPARISONS = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def filters_to_sql(
    filters: Filters | None,
    columns: Mapping[str, tuple[str, str]],
    prefix: str = "f",
) -> tuple[str, dict[str, Any]]:
    """Return (" AND ..." clause, params). `columns` maps a filter key to
    (SQL expression, Postgres type). Unknown keys or operators raise
    UnsupportedFilterError instead of being skipped."""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    def bind(value: Any) -> str:
        name = f"{prefix}{len(params)}"
        params[name] = value
        return f":{name}"

    for key, condition in (filters or {}).items():
        if key not in columns:
            raise UnsupportedFilterError(key)
        column, sql_type = columns[key]
        conditions = condition if isinstance(condition, Mapping) else {
            "in" if isinstance(condition, (list, tuple, set, frozenset)) else "eq": condition
        }
        for operator, value in conditions.items():
            if operator == "in":
                values = [str(v) for v in value]
                clauses.append(f"{column} = ANY(CAST({bind(values)} AS {sql_type}[]))")
            elif operator in _COMPARISONS:
                clauses.append(
                    f"{column} {_COMPARISONS[operator]} CAST({bind(str(value))} AS {sql_type})"
                )
            else:
                raise UnsupportedFilterError(f"{key}.{operator}")
    return "".join(f" AND {clause}" for clause in clauses), params

