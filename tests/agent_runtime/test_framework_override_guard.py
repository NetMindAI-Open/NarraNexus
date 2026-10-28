"""
@file_name: test_framework_override_guard.py
@date: 2026-08-06
@description: framework_override viability guard (review finding #5).

An override to nexus_power must not brick a turn that would have worked
on the user's slot framework: nexus_power hard-fails on OAuth
subscription credentials and on a missing model (the two raise paths in
NexusAgent._build_request_payload). When the current provider config
cannot serve nexus_power, the override is refused and slot resolution
stands — the voice turn then degrades through the existing legacy
finalize chain instead of dying.

Locks:
- Viable api-key anthropic slot -> override applies.
- OAuth credential or no model anywhere -> override refused.
- Non-nexus_power overrides pass through untouched (no policing).
- Configs are INJECTED (never mutate the shared ambient proxies — that
  polluted unrelated config tests when suites ran combined).
"""
from __future__ import annotations

from types import SimpleNamespace

from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _framework_override_viable,
)


def _cfg(model="", auth_type="api_key"):
    return SimpleNamespace(model=model, auth_type=auth_type)


def test_viable_with_api_key_anthropic_slot():
    assert _framework_override_viable(
        "nexus_power",
        claude=_cfg(model="deepseek-ai/DeepSeek-V4-Flash"),
        codex=_cfg(),
    ) is True


def test_refused_on_oauth_credentials():
    assert _framework_override_viable(
        "nexus_power",
        claude=_cfg(model="claude-sonnet-5", auth_type="oauth"),
        codex=_cfg(),
    ) is False


def test_refused_when_no_model_configured():
    assert _framework_override_viable(
        "nexus_power", claude=_cfg(), codex=_cfg()
    ) is False


def test_viable_via_openai_protocol_slot():
    assert _framework_override_viable(
        "nexus_power", claude=_cfg(), codex=_cfg(model="gpt-5")
    ) is True


def test_other_frameworks_pass_through():
    assert _framework_override_viable(
        "claude_code", claude=_cfg(), codex=_cfg()
    ) is True


def test_oauth_claude_shortcircuits_even_with_codex_model():
    """Review finding #15: _resolve_provider is claude-first short-circuit —
    a non-empty claude.model means codex is NEVER consulted. The guard must
    mirror the priority, not just the conditions, or it answers 'viable'
    for a config the adapter will hard-fail on."""
    assert _framework_override_viable(
        "nexus_power",
        claude=_cfg(model="claude-sonnet-5", auth_type="oauth"),
        codex=_cfg(model="gpt-5"),
    ) is False
