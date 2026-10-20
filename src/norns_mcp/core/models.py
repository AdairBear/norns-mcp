"""Shared result type. Same shape as ``m8_mcp.models.ToolResult``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    """Unified response payload for every controller operation."""

    success: bool
    message: str
    data: dict[str, Any] | None = None
