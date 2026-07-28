"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-07-24
@description: Platform-side analytics — vendor sink implementations.

The kernel (xyz_agent_context.analytics) owns the capture API, gating,
pseudonymization and the NullSink default; vendor sinks are a platform
concern and live here. backend/main.py calls register_posthog_sink()
at import time so every route's track() resolves through the seam.
"""
from typing import Optional

from xyz_agent_context.analytics import register_sink_factory
from xyz_agent_context.analytics.base import AnalyticsClient


def _posthog_factory() -> Optional[AnalyticsClient]:
    """Factory the kernel calls once gating passed: PostHog when a key is
    configured, else None (kernel falls back to NullSink)."""
    import os

    key = os.environ.get("POSTHOG_API_KEY")
    if not key:
        return None
    from backend.analytics.posthog_sink import PostHogSink

    return PostHogSink(api_key=key, host=os.environ.get("POSTHOG_HOST"))


def register_posthog_sink() -> None:
    register_sink_factory(_posthog_factory)
