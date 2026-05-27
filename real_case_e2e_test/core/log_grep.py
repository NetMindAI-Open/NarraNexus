"""
@file_name: log_grep.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Pulls backend log lines correlated to one run via run_id

The local stack today writes loguru output to stdout (captured by tmux
or whatever supervises ``bash run.sh``). We do NOT assume a file path —
that would couple this module to run.sh and tmux. Instead we accept an
optional log path via env (``NN_E2E_BACKEND_LOG``); when absent we
return an empty slice and the report flags the missing log so the
semantic phase knows not to expect it.

This is intentional: backend log enrichment is best-effort. The
transcript itself already contains every event the agent emitted; logs
are extra colour (model resolution, TIMED steps, NO-REPLY-FALLBACK).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional


# Override via NN_E2E_BACKEND_LOG=/path/to/backend.log
ENV_VAR = "NN_E2E_BACKEND_LOG"


def backend_log_path() -> Optional[Path]:
    raw = os.environ.get(ENV_VAR, "").strip()
    if not raw:
        return None
    p = Path(raw)
    return p if p.exists() else None


def slice_by_run_id(run_id: str, *, log_path: Optional[Path] = None) -> list[str]:
    """Return every log line whose payload mentions the given run_id.

    Cheap implementation (full scan); fine for a few-thousand-line
    debug session. If the log file grows to many MB across many cases
    we revisit with an index. The run_id is a unique UUID prefix so
    matching is essentially free.
    """
    if log_path is None:
        log_path = backend_log_path()
    if log_path is None or not run_id:
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
            return [line.rstrip() for line in fh if run_id in line]
    except OSError:
        return []
