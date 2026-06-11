"""
@file_name: base.py
@date: 2026-06-08
@description: Vendor-agnostic analytics client protocol.

Every sink (PostHog, Null, Fake) implements this. Callers depend only on
this protocol, never on `posthog` directly — keeps us one swap away from
any vendor (binding rule #9). capture/identify are sync and best-effort
(must never raise into the caller — observer never breaks observed).
"""
from __future__ import annotations

from typing import Optional, Protocol


class AnalyticsClient(Protocol):
    def capture(self, *, distinct_id: str, event: str,
                properties: Optional[dict] = None) -> None: ...

    def identify(self, *, distinct_id: str, traits: Optional[dict] = None) -> None: ...

    def flush(self) -> None: ...
