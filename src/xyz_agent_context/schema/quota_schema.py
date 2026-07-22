"""
@file_name: quota_schema.py
@author: Bin Liang
@date: 2026-04-16
@description: Quota data model for system-default free-tier token budget.

Tracks per-user consumption of the system-provided NetMind key. Separate
columns for input and output tokens because the two differ in price by ~5x;
a unified counter would give staff no insight into real USD cost.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class QuotaStatus(str, Enum):
    ACTIVE = "active"
    EXHAUSTED = "exhausted"
    DISABLED = "disabled"


class Quota(BaseModel):
    user_id: str
    initial_input_tokens: int = Field(ge=0)
    initial_output_tokens: int = Field(ge=0)
    used_input_tokens: int = Field(default=0, ge=0)
    used_output_tokens: int = Field(default=0, ge=0)
    granted_input_tokens: int = Field(default=0, ge=0)
    granted_output_tokens: int = Field(default=0, ge=0)
    status: QuotaStatus = QuotaStatus.ACTIVE
    # Exhaustion-notice dedup latch — NOT a routing preference. The old
    # user-facing "prefer free tier" toggle was removed 2026-07-18
    # (free-tier-first is platform behavior; see provider_resolver):
    #   1 (armed) = the next exhausted run that falls through to the user's
    #     own provider emits the one-time "switched to your own key" notice
    #     (CAS 1→0, winner notifies).
    #   0 (fired) = the notice went out this cycle; re-armed 0→1 on the
    #     next run that finds budget again (staff grant).
    # Default True: new rows start armed. Column name kept (rule #6 — no
    # dangerous schema changes).
    prefer_system_override: bool = True
    created_at: datetime
    updated_at: datetime

    @property
    def remaining_input(self) -> int:
        return max(
            0,
            self.initial_input_tokens
            + self.granted_input_tokens
            - self.used_input_tokens,
        )

    @property
    def remaining_output(self) -> int:
        return max(
            0,
            self.initial_output_tokens
            + self.granted_output_tokens
            - self.used_output_tokens,
        )

    def has_budget(self) -> bool:
        return (
            self.status == QuotaStatus.ACTIVE
            and self.remaining_input > 0
            and self.remaining_output > 0
        )
