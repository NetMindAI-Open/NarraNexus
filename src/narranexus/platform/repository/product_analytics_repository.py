"""
@file_name: product_analytics_repository.py
@author: NarraNexus
@date: 2026-08-10
@description: Atomic persistence for first-party product analytics facts.

Known dimensions are stored in queryable columns while the complete controlled
property map is retained as compact JSON for low-volume diagnosis. Callers must
never pass conversation content, credentials, email, or other free-form PII.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from narranexus.platform.utils import AsyncDatabaseClient
from narranexus.platform.utils.db.dialect_errors import is_unique_violation


class ProductAnalyticsRepository:
    """Write-only repository for ``product_analytics_events``."""

    table_name = "product_analytics_events"

    def __init__(self, db: AsyncDatabaseClient) -> None:
        self.db = db

    async def record(
        self,
        *,
        user_id: str,
        event: str,
        event_id: str | None,
        properties: dict[str, Any],
        occurred_at: str | None = None,
    ) -> None:
        analytics_event_id = event_id or str(uuid.uuid4())

        def _string(key: str) -> str | None:
            value = properties.get(key)
            return str(value) if value is not None and value != "" else None

        latency = properties.get("latency_ms")
        try:
            latency_ms = int(latency) if latency is not None else None
        except (TypeError, ValueError):
            latency_ms = None

        row: dict[str, Any] = {
            "analytics_event_id": analytics_event_id,
            "event_name": event,
            "user_id": user_id,
            "source": _string("source") or "backend",
            "surface": _string("surface"),
            "session_id": _string("session_id"),
            "run_id": _string("run_id"),
            "agent_id": _string("agent_id"),
            "trigger_source": _string("trigger_source"),
            "provider_card_source": _string("provider_card_source"),
            "failure_category": _string("failure_category"),
            "failure_reason": _string("failure_reason"),
            "latency_ms": latency_ms,
            "properties_json": json.dumps(
                properties,
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        }
        if occurred_at is not None:
            row["occurred_at"] = occurred_at

        # Insert first and let the unique key arbitrate concurrent retries.
        # The first fact wins; a replay cannot overwrite its dimensions.
        try:
            await self.db.insert(self.table_name, row)
        except Exception as exc:
            if is_unique_violation(exc):
                return
            raise
