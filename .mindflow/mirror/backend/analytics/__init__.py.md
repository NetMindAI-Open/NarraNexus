---
code_file: backend/analytics/__init__.py
last_verified: 2026-07-24
stub: false
---

# backend/analytics/__init__.py — platform side of the analytics split

## Why it exists

B4 of the agent/platform split: the kernel
(`xyz_agent_context/analytics`) owns the capture API, gating,
pseudonymization and NullSink; vendor sinks (PostHog) are platform
integrations and belong backend-side. This package holds the PostHog
factory and `register_posthog_sink()`, which backend/main.py calls at
import time so the seam is installed before any route fires track().

## Design decisions

- **Factory returns None when POSTHOG_API_KEY is unset** — the kernel
  maps None to NullSink, so key-gating behavior is byte-identical to
  the pre-split lazy import.
- **Kernel gates still win**: NARRA_ANALYTICS_ENABLED=false or
  SURFACE=cloud short-circuit before the factory is even called
  (covered by tests/analytics/test_factory_gating.py).
