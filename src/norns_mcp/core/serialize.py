"""JSON-safe serialization of controller results (lifted from mcp-daw-engine-tracker)."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any


def normalize_value(value: Any) -> Any:
    """Recursively convert dataclasses, enums, Paths and containers to JSON-safe values."""
    if isinstance(value, Enum):
        return normalize_value(value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: normalize_value(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(k): normalize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [normalize_value(v) for v in value]
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, bytes):
        return value.hex(" ").upper()
    return value


def serialize_result(result: Any) -> dict[str, Any]:
    """Serialize a ``ToolResult`` (or any dataclass) to a plain dict."""
    out = normalize_value(result)
    if not isinstance(out, dict):
        raise TypeError(f"cannot serialize {type(result).__name__} to a dict")
    return out
