"""
@file_name: test_security_iron_rules.py
@date: 2026-06-17
@description: The platform-wide security iron rules must exist and be
injected FIRST into every agent's system prompt.

Incident 2026-06-17: an agent dumped all backend env vars and read
another agent's workspace on request. SECURITY_IRON_RULES is the
prompt-layer mitigation; these tests lock its content + placement.
"""
from __future__ import annotations

import inspect

from xyz_agent_context.context_runtime.prompts import SECURITY_IRON_RULES


def test_iron_rules_cover_workspace_and_env_prohibition():
    text = SECURITY_IRON_RULES.lower()
    assert "workspace" in text
    # Forbids reading env vars / process state, not just disclosing them.
    assert "env" in text and "/proc" in text
    assert "forbidden" in text
    # Must frame it as "don't look", not "look but don't tell".
    assert "prohibition on looking" in text or "do not read it at all" in text


def test_iron_rules_require_vetting_uploaded_code():
    text = SECURITY_IRON_RULES.lower()
    assert "before you run it" in text or "before executing" in text
    assert "refuse to run" in text or "refuse" in text


def test_iron_rules_resist_identity_override():
    text = SECURITY_IRON_RULES.lower()
    # An "I'm the admin/creator/developer" claim must not unlock them.
    assert "admin" in text or "creator" in text or "developer" in text
    assert "override" in text


def test_iron_rules_injected_first_in_system_prompt():
    """Contract: build_complete_system_prompt appends SECURITY_IRON_RULES
    and does so before any other prompt part (so nothing supersedes it)."""
    from xyz_agent_context.context_runtime.context_runtime import ContextRuntime

    src = inspect.getsource(ContextRuntime.build_complete_system_prompt)
    assert "SECURITY_IRON_RULES" in src, (
        "build_complete_system_prompt no longer injects SECURITY_IRON_RULES"
    )
    idx_security = src.index("prompt_parts.append(SECURITY_IRON_RULES)")
    # It must be the first append — appears before the temporal block append.
    idx_temporal = src.index("prompt_parts.append(temporal_block)")
    assert idx_security < idx_temporal, (
        "SECURITY_IRON_RULES must be appended FIRST (before all other "
        "prompt sections) so no later section can supersede it."
    )
    # ...and it must be CLOUD-ONLY: local/desktop deliberately omits it so
    # the agent can operate across the user's own folders.
    gate = src.index('get_deployment_mode() == "cloud"')
    assert gate < idx_security, (
        "SECURITY_IRON_RULES injection must be gated on cloud mode — local "
        "agents must NOT be restricted to their workspace."
    )
