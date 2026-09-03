"""Coercion helpers for reading loosely-typed session state and tool returns."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_str_list(value: Any) -> list[str]:
    """Stringify a list, dropping blanks. Non-lists become an empty list."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def dedupe(items: Iterable[str]) -> list[str]:
    """Drop repeated entries, preserving first-seen order."""
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique
