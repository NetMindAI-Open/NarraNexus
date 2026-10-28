"""
@file_name: dialect_time.py
@author:
@date: 2026-08-10
@description: DATETIME cell normalization across DB dialects.

sqlite's driver returns ``datetime`` objects for DATETIME-typed cells;
mysql returns strings. Any code that sorts or compares timestamp cells
must first normalize — this asymmetry is a property of the DB layer,
not of any one table, which is why the helper lives here rather than
inside an audit repository (it had been copy-pasted into two of them,
and a route module had started importing one copy's private name).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def event_time_str(value: Any) -> str:
    """Normalize a DATETIME cell to a sortable ISO string (space form)."""
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    return str(value or "")
