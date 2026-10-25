"""Metadata filter matching shared by the vector store and BM25 index.

A filter spec maps a metadata key to a condition:
    {"document_id": "sc-2017-0042"}                 equality (or membership if the
                                                    stored value is a list)
    {"document_id": ["a", "b"]}                     value in list
    {"decision_date": {"gte": "2010-01-01"}}        operators: eq, in, gt, gte, lt, lte,
                                                     contains (case-insensitive substring)
Chunks missing a filtered key never match.
"""

from collections.abc import Mapping
from typing import Any

Filters = Mapping[str, Any]

_OPERATORS = {"eq", "in", "gt", "gte", "lt", "lte", "contains"}


def _check(op: str, stored: Any, expected: Any) -> bool:
    if op == "eq":
        return expected in stored if isinstance(stored, (list, tuple)) else stored == expected
    if op == "in":
        if isinstance(stored, (list, tuple)):
            return any(item in expected for item in stored)
        return stored in expected
    if op == "contains":
        return str(expected).casefold() in str(stored).casefold()
    try:
        if op == "gt":
            return stored > expected
        if op == "gte":
            return stored >= expected
        if op == "lt":
            return stored < expected
        if op == "lte":
            return stored <= expected
    except TypeError:
        return False
    raise ValueError(f"Unknown filter operator: {op!r}")


def matches(metadata: Mapping[str, Any], filters: Filters | None) -> bool:
    if not filters:
        return True
    for key, condition in filters.items():
        if key not in metadata or metadata[key] is None:
            return False
        stored = metadata[key]
        if isinstance(condition, Mapping):
            unknown = set(condition) - _OPERATORS
            if unknown:
                raise ValueError(f"Unknown filter operators for {key!r}: {sorted(unknown)}")
            if not all(_check(op, stored, expected) for op, expected in condition.items()):
                return False
        elif isinstance(condition, (list, tuple, set, frozenset)):
            if not _check("in", stored, condition):
                return False
        elif not _check("eq", stored, condition):
            return False
    return True

