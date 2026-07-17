"""Shared numeric helper for the surviving Nova finance modules.

The general-ledger aggregation helpers that lived here (`_build_history_pipeline`,
`_start_of_next_month`, `_GL_QUERY_LIMIT`) were removed in Phase 1 (accounting-
integration removal) along with the external data lake they queried. Only
``_safe_float`` survives — it is a generic coercion used by ``finance_snapshots.py``.
"""
from __future__ import annotations

from typing import Any


def _safe_float(value: Any) -> float:
    """Best-effort float coercion that returns 0.0 for None or
    unparseable values."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["_safe_float"]