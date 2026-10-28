"""
@file_name: job.py
@author: Bin Liang
@date: 2026-09-04
@description: Shared job types crossing the platform/plugin boundary (the run-once outcome).

``JobRunOutcome`` is what the ``jobs.run_once`` service (builtin.job) returns
and what the Manyfold sync route streams back; keeping the shape here lets
the route depend on the contract instead of the job module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class JobRunOutcome:
    job_id: str
    ok: bool
    reason: Optional[str] = None
    status: Optional[str] = None
    drained: int = 0


__all__ = ["JobRunOutcome"]
