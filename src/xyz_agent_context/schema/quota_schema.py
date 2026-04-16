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
    # When True, the user has opted in to routing LLM calls through the
    # system-default free-tier provider even when they have their own
    # provider configured. Respects the same quota gating as the
    # no-config fallback path — running out still raises / returns 402.
    prefer_system_override: bool = False
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
