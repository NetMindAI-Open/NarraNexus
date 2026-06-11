"""
@file_name: posthog_sink.py
@date: 2026-06-08
@description: PostHog implementation of AnalyticsClient.

Wraps posthog-python's module-level client. capture/identify are
best-effort: any error is logged and swallowed (observer never breaks
observed). posthog batches on a background thread; flush() drains it on
shutdown.
"""
from __future__ import annotations

from typing import Optional

from loguru import logger


class PostHogSink:
    def __init__(self, api_key: str, host: Optional[str] = None) -> None:
        import posthog
        self._ph = posthog.Posthog(
            project_api_key=api_key,
            host=host or "https://us.i.posthog.com",
        )

    def capture(self, *, distinct_id: str, event: str,
                properties: Optional[dict] = None) -> None:
        try:
            self._ph.capture(distinct_id=distinct_id, event=event,
                             properties=properties or {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[analytics] capture {event} failed: {e}")

    def identify(self, *, distinct_id: str, traits: Optional[dict] = None) -> None:
        # posthog-python 7.x dropped Client.identify(); person properties are
        # set via set() ("Set properties on a person profile").
        try:
            self._ph.set(distinct_id=distinct_id, properties=traits or {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[analytics] identify failed: {e}")

    def flush(self) -> None:
        try:
            self._ph.flush()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[analytics] flush failed: {e}")
