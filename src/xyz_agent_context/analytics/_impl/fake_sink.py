"""
@file_name: fake_sink.py
@date: 2026-06-08
@description: In-memory AnalyticsClient for tests. Records every call so
tests can assert which funnel events fired with which properties.
"""
from __future__ import annotations

from typing import Optional


class FakeSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, Optional[dict]]] = []
        self.identities: list[tuple[str, Optional[dict]]] = []
        self.flushed = 0

    def capture(self, *, distinct_id: str, event: str,
                properties: Optional[dict] = None) -> None:
        self.events.append((distinct_id, event, properties))

    def identify(self, *, distinct_id: str, traits: Optional[dict] = None) -> None:
        self.identities.append((distinct_id, traits))

    def flush(self) -> None:
        self.flushed += 1
