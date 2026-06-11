"""
@file_name: null_sink.py
@date: 2026-06-08
@description: No-op AnalyticsClient. Used when analytics is disabled
(no key / NARRA_ANALYTICS_ENABLED=false / surface=cloud this phase) and
as the default in tests.
"""
from __future__ import annotations

from typing import Optional


class NullSink:
    def capture(self, *, distinct_id: str, event: str,
                properties: Optional[dict] = None) -> None:
        return None

    def identify(self, *, distinct_id: str, traits: Optional[dict] = None) -> None:
        return None

    def flush(self) -> None:
        return None
