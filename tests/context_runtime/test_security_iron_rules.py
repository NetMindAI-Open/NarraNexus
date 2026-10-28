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

from narranexus.platform.context_runtime.prompts import SECURITY_IRON_RULES


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
    """Contract (2026-09-07, the prompt is a slot): the builtin `security` section
    renders SECURITY_IRON_RULES on cloud only, and sorts FIRST among the builtin
    sections (lowest order) so no later section can supersede it."""
    import asyncio
    from types import SimpleNamespace

    from narranexus.contracts.prompt import PromptContext
    from narranexus.kernel.plugins.builtins import load_builtins, slot_tree_with_builtins
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform.context_runtime.prompts import SECURITY_IRON_RULES
    from narranexus.platform.prompt_slots import sections_for

    regs = Registries(slot_tree_with_builtins())
    load_builtins(regs, "backend")
    providers = sections_for(regs)
    assert providers[0].id == "security", "the security section must be the first builtin section"
    assert providers[0].order < min(p.order for p in providers[1:])

    def ctx(mode):
        return PromptContext(agent_id="a", user_id="u", ctx_data=SimpleNamespace(), narrative_list=[], selected_events=[],
                             module_instructions=[], db=None, runtime=None, deployment_mode=mode)

    assert asyncio.run(providers[0].render(ctx("cloud"))) == SECURITY_IRON_RULES
    assert asyncio.run(providers[0].render(ctx("local"))) is None, "local agents must NOT be restricted to their workspace"
