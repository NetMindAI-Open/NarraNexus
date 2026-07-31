"""
@file_name: test_turn_context_split.py
@author: NarraNexus
@date: 2026-07-25
@description: R4b — per-module relocation of per-turn volatile spans from
get_instructions into get_turn_context.

Locks the uniform R4b module contract for the six capability modules whose
instructions used to change every turn (BasicInfo / GeneralMemory /
SocialNetwork / Job / MessageBus / CommonTools):

- flag ON  → get_instructions is BYTE-STABLE across consecutive turns for a
  fixed module config: two builds where only the volatile state differs
  (time, recalled memories, entity card, jobs table, bus lists, attachments)
  produce identical bytes — the core new guarantee that makes the system
  prompt prefix cacheable;
- flag ON  → get_turn_context carries the moved span (verbatim-equivalent
  wording, stable per-module heading) — relocated, never dropped (铁律 #16);
- flag OFF → get_instructions renders the legacy full text, byte-identical
  to the pre-R4 layout;
- get_turn_context is fail-open at the module level: a bare ContextData
  (hooks never ran, volatile fields unset) yields "" without raising.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.schema import ContextData
from xyz_agent_context.settings import settings

AGENT_ID = "agent_tcs"
USER_ID = "user_tcs"


def _ctx(**fields) -> ContextData:
    extra = fields.pop("extra_data", {})
    ctx = ContextData(
        agent_id=AGENT_ID,
        user_id=USER_ID,
        input_content="hello",
        **fields,
    )
    ctx.extra_data = extra
    return ctx


@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)


@pytest.fixture
def flag_off(monkeypatch):
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", False)


# =========================================================================
# BasicInfoModule — volatile span: {current_time}
# =========================================================================

def _basic_info_module():
    from xyz_agent_context.module.basic_info_module.basic_info_module import (
        BasicInfoModule,
    )
    return BasicInfoModule(AGENT_ID, USER_ID, None)


def _basic_ctx(current_time: str) -> ContextData:
    return _ctx(
        current_time=current_time,
        agent_name="Testy",
        agent_description="a test agent",
        creator_name="Owner",
        is_creator=True,
        current_speaker_name="Owner",
        user_role="Creator (Boss)",
        agent_info_model_type="Claude Agent SDK",
        model_name="sonnet",
        deployment_context="##### Deployment: LOCAL (test)",
    )


def test_basic_info_stable_template_anchors():
    """The stable template is derived by replacing the exact volatile span —
    guard the anchor so a wording edit to the legacy template can't silently
    turn the replace into a no-op."""
    from xyz_agent_context.module.basic_info_module.prompts import (
        BASIC_INFO_MODULE_INSTRUCTIONS,
        BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
        BASIC_INFO_REAL_WORLD_TURN_TEMPLATE,
    )
    assert BASIC_INFO_REAL_WORLD_TURN_TEMPLATE in BASIC_INFO_MODULE_INSTRUCTIONS
    assert "{current_time}" not in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE
    assert "##### Real World Information" in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE
    # Non-volatile placeholders stay in the stable template (speaker changes
    # are a legitimate cache break, not per-turn volatility).
    for placeholder in ("{agent_id}", "{current_speaker_name}", "{deployment_context}"):
        assert placeholder in BASIC_INFO_MODULE_INSTRUCTIONS_STABLE


@pytest.mark.asyncio
async def test_basic_info_instructions_byte_stable_when_flag_on(flag_on):
    mod = _basic_info_module()
    out1 = await mod.get_instructions(_basic_ctx("2026-07-25 10:00:00 +08:00 (Saturday, Asia/Shanghai)"))
    out2 = await mod.get_instructions(_basic_ctx("2026-07-25 10:07:42 +08:00 (Saturday, Asia/Shanghai)"))
    assert out1 == out2
    assert "10:00:00" not in out1
    # Static identity/session content still renders.
    assert f"`{AGENT_ID}`" in out1
    assert "##### Deployment: LOCAL (test)" in out1


@pytest.mark.asyncio
async def test_basic_info_turn_context_carries_time_and_ground_truth(flag_on):
    mod = _basic_info_module()
    t = "2026-07-25 10:00:00 +08:00 (Saturday, Asia/Shanghai)"
    block = await mod.get_turn_context(_basic_ctx(t))
    assert block.startswith("##### Real World Information")
    assert f"- Current date and time: {t}" in block
    # Anti-hallucination guidance moved verbatim, not dropped.
    assert "**ground truth**" in block
    assert "FUTURE" in block and "PAST" in block


@pytest.mark.asyncio
async def test_basic_info_flag_off_is_legacy_byte_identical(flag_off):
    from xyz_agent_context.module.basic_info_module.prompts import (
        BASIC_INFO_MODULE_INSTRUCTIONS,
    )
    mod = _basic_info_module()
    ctx = _basic_ctx("2026-07-25 10:00:00 +08:00 (Saturday, Asia/Shanghai)")
    out = await mod.get_instructions(ctx)
    assert out == BASIC_INFO_MODULE_INSTRUCTIONS.format(**ctx.model_copy().model_dump())
    assert "- Current date and time: 2026-07-25 10:00:00" in out


@pytest.mark.asyncio
async def test_basic_info_turn_context_fail_open_on_bare_ctx(flag_on):
    assert await _basic_info_module().get_turn_context(_ctx()) == ""


# =========================================================================
# GeneralMemoryModule — volatile span: the whole recalled-memories body
# =========================================================================

def _memory_module():
    from xyz_agent_context.module.general_memory_module.general_memory_module import (
        GeneralMemoryModule,
    )
    return GeneralMemoryModule(AGENT_ID, USER_ID, None)


def _memory_ctx(memories) -> ContextData:
    return _ctx(extra_data={"relevant_memories": memories})


@pytest.mark.asyncio
async def test_memory_instructions_byte_stable_when_flag_on(flag_on):
    mod = _memory_module()
    out1 = await mod.get_instructions(_memory_ctx(["[observation] (2026-07-24 09:00) Alice works at Google"]))
    out2 = await mod.get_instructions(_memory_ctx(["[observation] (2026-07-25 11:30) Bob prefers terse replies"]))
    out_empty = await mod.get_instructions(_memory_ctx([]))
    # Constant bytes — including the previously flapping empty-recall turn.
    assert out1 == out2 == out_empty
    assert out1.startswith("## What you remember")
    assert "Alice" not in out1


@pytest.mark.asyncio
async def test_memory_turn_context_carries_recalled_list(flag_on):
    mod = _memory_module()
    block = await mod.get_turn_context(
        _memory_ctx(["[observation] (2026-07-24 09:00) Alice works at Google"])
    )
    assert block.startswith("## What you remember")
    assert "- [observation] (2026-07-24 09:00) Alice works at Google" in block
    assert "trust the most recent one" in block
    # No recall → nothing to contribute this turn.
    assert await mod.get_turn_context(_memory_ctx([])) == ""


@pytest.mark.asyncio
async def test_memory_flag_off_is_legacy_byte_identical(flag_off):
    mod = _memory_module()
    out = await mod.get_instructions(
        _memory_ctx(["[observation] (2026-07-24 09:00) Alice works at Google"])
    )
    assert out == (
        "## What you remember\n"
        "Things you have learned that may be relevant now. Each item is tagged "
        "with when you learned it (UTC); when two memories about the same thing "
        "disagree, trust the most recent one.\n"
        "- [observation] (2026-07-24 09:00) Alice works at Google\n"
    )
    # Legacy empty-recall behavior: empty string, not a header.
    assert await mod.get_instructions(_memory_ctx([])) == ""


@pytest.mark.asyncio
async def test_memory_turn_context_fail_open_on_bare_ctx(flag_on):
    assert await _memory_module().get_turn_context(_ctx()) == ""


# =========================================================================
# SocialNetworkModule — volatile span: §5 current-entity card
# =========================================================================

def _social_module():
    from xyz_agent_context.module.social_network_module.social_network_module import (
        SocialNetworkModule,
    )
    return SocialNetworkModule(AGENT_ID, USER_ID, None)


def _entity_card(count: int) -> str:
    return (
        "**You already know this user:**\n"
        f"- Previous interactions: {count}\n"
        "- Last contact: 2026-07-25"
    )


def test_social_stable_template_anchors():
    from xyz_agent_context.module.social_network_module.prompts import (
        SOCIAL_NETWORK_MODULE_INSTRUCTIONS,
        SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE,
    )
    assert "{social_network_current_entity}" in SOCIAL_NETWORK_MODULE_INSTRUCTIONS
    assert "{social_network_current_entity}" not in SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE
    # §1-4/§6 rules stay; §5 heading survives as a static pointer.
    assert "##### 5. Current User Information" in SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE
    assert "##### 6. Behavior Expectations" in SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE


@pytest.mark.asyncio
async def test_social_instructions_byte_stable_when_flag_on(flag_on):
    mod = _social_module()
    out1 = await mod.get_instructions(_ctx(social_network_current_entity=_entity_card(3)))
    out2 = await mod.get_instructions(_ctx(social_network_current_entity=_entity_card(4)))
    assert out1 == out2
    assert "Previous interactions" not in out1
    # agent_id baked, {{...}} escapes rendered as in the legacy path.
    assert f'agent_id="{AGENT_ID}"' in out1
    assert "entity_{name}_{timestamp}" in out1


@pytest.mark.asyncio
async def test_social_turn_context_carries_entity_card(flag_on):
    mod = _social_module()
    block = await mod.get_turn_context(_ctx(social_network_current_entity=_entity_card(3)))
    assert block.startswith("##### Current User Information\n")
    assert "- Previous interactions: 3" in block

    # Hook fallback texts ride the same channel (first-meeting card here).
    fallback = "**First time meeting this user.**"
    block2 = await mod.get_turn_context(_ctx(social_network_current_entity=fallback))
    assert fallback in block2


@pytest.mark.asyncio
async def test_social_flag_off_is_legacy_byte_identical(flag_off):
    from xyz_agent_context.module.social_network_module.prompts import (
        SOCIAL_NETWORK_MODULE_INSTRUCTIONS,
    )
    mod = _social_module()
    ctx = _ctx(social_network_current_entity=_entity_card(3))
    out = await mod.get_instructions(ctx)
    legacy_template = SOCIAL_NETWORK_MODULE_INSTRUCTIONS.replace("{agent_id}", AGENT_ID)
    assert out == legacy_template.format(**ctx.model_copy().model_dump())
    assert "- Previous interactions: 3" in out


@pytest.mark.asyncio
async def test_social_turn_context_fail_open_on_bare_ctx(flag_on):
    assert await _social_module().get_turn_context(_ctx()) == ""


# =========================================================================
# JobModule — volatile span: the "Current Job Status" {jobs_information} table
# =========================================================================

def _job_module():
    from xyz_agent_context.module.job_module.job_module import JobModule
    return JobModule(AGENT_ID, USER_ID, None)


_JOBS_TABLE_A = (
    "###### Active Jobs (1)\n\n"
    "| Title | ID | Status | Trigger |\n"
    "|-------|-----|--------|---------|\n"
    "| daily digest | `job_aaaa1111` | active | every 86400s |"
)
_JOBS_TABLE_B = _JOBS_TABLE_A.replace("job_aaaa1111", "job_bbbb2222")


def test_job_stable_template_anchors():
    from xyz_agent_context.module.job_module.job_module import (
        JOB_MODULE_INSTRUCTIONS,
        JOB_MODULE_INSTRUCTIONS_STABLE,
    )
    assert "{jobs_information}" in JOB_MODULE_INSTRUCTIONS
    assert "{jobs_information}" not in JOB_MODULE_INSTRUCTIONS_STABLE
    # Location wording corrected: nothing points "above" anymore.
    assert "If there are jobs listed above:" in JOB_MODULE_INSTRUCTIONS
    assert "listed above" not in JOB_MODULE_INSTRUCTIONS_STABLE
    assert "If there are jobs listed in the turn context:" in JOB_MODULE_INSTRUCTIONS_STABLE
    # The static guide stays.
    assert "##### Job Creation Rules" in JOB_MODULE_INSTRUCTIONS_STABLE
    assert "##### Job Modification Permissions" in JOB_MODULE_INSTRUCTIONS_STABLE


@pytest.mark.asyncio
async def test_job_instructions_byte_stable_when_flag_on(flag_on):
    mod = _job_module()
    out1 = await mod.get_instructions(_ctx(jobs_information=_JOBS_TABLE_A))
    out2 = await mod.get_instructions(_ctx(jobs_information=_JOBS_TABLE_B))
    assert out1 == out2
    assert "job_aaaa1111" not in out1
    assert "##### Current Job Status" in out1  # static pointer keeps the heading


@pytest.mark.asyncio
async def test_job_turn_context_carries_jobs_table(flag_on):
    mod = _job_module()
    block = await mod.get_turn_context(_ctx(jobs_information=_JOBS_TABLE_A))
    assert block.startswith("##### Current Job Status\n\n")
    assert "`job_aaaa1111`" in block
    # The empty-state line is content too — relocated, not dropped.
    empty = await mod.get_turn_context(_ctx(jobs_information="*No jobs for this conversation.*"))
    assert "*No jobs for this conversation.*" in empty


@pytest.mark.asyncio
async def test_job_flag_off_is_legacy_byte_identical(flag_off):
    from xyz_agent_context.module.job_module.job_module import JOB_MODULE_INSTRUCTIONS
    mod = _job_module()
    ctx = _ctx(jobs_information=_JOBS_TABLE_A)
    out = await mod.get_instructions(ctx)
    assert out == JOB_MODULE_INSTRUCTIONS.format(**ctx.model_copy().model_dump())
    assert "`job_aaaa1111`" in out
    assert "If there are jobs listed above:" in out


@pytest.mark.asyncio
async def test_job_turn_context_fail_open_on_bare_ctx(flag_on):
    assert await _job_module().get_turn_context(_ctx()) == ""


# =========================================================================
# MessageBusModule — volatile spans: Known Agents / Your Channels / Unread
# =========================================================================

def _bus_module():
    from xyz_agent_context.module.message_bus_module.message_bus_module import (
        MessageBusModule,
    )
    return MessageBusModule(AGENT_ID, USER_ID, None)


def _bus_ctx(n_unread: int = 1) -> ContextData:
    return _ctx(extra_data={
        "bus_known_agents": [
            {"agent_id": "agent_peer", "agent_name": "Peer", "agent_description": "helper"},
        ],
        "bus_channels": [
            {"channel_id": "ch_1", "name": "Sync", "channel_type": "group"},
        ],
        "bus_unread_messages": [
            {"from_agent": "agent_peer", "channel_id": "ch_1", "content": f"ping {i}"}
            for i in range(n_unread)
        ],
    })


@pytest.mark.asyncio
async def test_bus_instructions_byte_stable_when_flag_on(flag_on):
    mod = _bus_module()
    out1 = await mod.get_instructions(_bus_ctx(n_unread=1))
    out2 = await mod.get_instructions(_bus_ctx(n_unread=3))
    out_empty = await mod.get_instructions(_ctx())
    assert out1 == out2 == out_empty
    # Usage rules stay in the stable half...
    assert "### Reply Discipline — CRITICAL (prevents infinite loops)" in out1
    assert f"Your agent ID: `{AGENT_ID}`" in out1
    # ...the live lists do not.
    assert "### Known Agents" not in out1
    assert "### Unread Messages" not in out1


@pytest.mark.asyncio
async def test_bus_turn_context_carries_lists(flag_on):
    mod = _bus_module()
    block = await mod.get_turn_context(_bus_ctx(n_unread=2))
    assert block.startswith("### MessageBus — Current State")
    assert "### Known Agents (top 1)" in block
    assert "- `agent_peer` — Peer: helper" in block
    assert "### Your Channels (top 1)" in block
    assert "- `ch_1` — Sync (group)" in block
    assert "### Unread Messages: 2 (showing 2)" in block
    assert "- `[MessageBus · agent_peer · ch_1]` ping 0" in block
    # Nothing live → no block.
    assert await mod.get_turn_context(_ctx()) == ""


@pytest.mark.asyncio
async def test_bus_flag_off_is_legacy_byte_identical(flag_off):
    mod = _bus_module()
    ctx = _bus_ctx(n_unread=1)
    out = await mod.get_instructions(ctx)
    # Pre-R4 rendering = static rules + the three lists in one block.
    expected = "\n".join(
        mod._static_instruction_parts() + mod._volatile_context_parts(ctx)
    )
    assert out == expected
    assert "### Known Agents (top 1)" in out
    assert "### Unread Messages: 1 (showing 1)" in out


# =========================================================================
# CommonToolsModule — volatile spans: attachments appendix + artifact registry
# =========================================================================

def _tools_module(db=None):
    from xyz_agent_context.module.common_tools_module.common_tools_module import (
        CommonToolsModule,
    )
    return CommonToolsModule(AGENT_ID, USER_ID, db)


def _attachment_ctx(name: str) -> ContextData:
    return _ctx(extra_data={"attachments": [
        {"file_id": "f_1", "original_name": name, "mime_type": "text/plain"},
    ]})


@pytest.mark.asyncio
async def test_tools_instructions_byte_stable_when_flag_on(flag_on, db_client):
    from xyz_agent_context.module.common_tools_module.common_tools_module import (
        COMMON_TOOLS_INSTRUCTIONS,
    )
    mod = _tools_module(db_client)
    out1 = await mod.get_instructions(_attachment_ctx("report.txt"))
    out2 = await mod.get_instructions(_attachment_ctx("notes.txt"))
    out_bare = await mod.get_instructions(_ctx())
    assert out1 == out2 == out_bare == COMMON_TOOLS_INSTRUCTIONS
    assert "report.txt" not in out1
    assert "Your registered artifacts" not in out1


@pytest.mark.asyncio
async def test_tools_turn_context_carries_attachments_and_registry(flag_on, db_client):
    mod = _tools_module(db_client)
    block = await mod.get_turn_context(_attachment_ctx("report.txt"))
    assert "#### Files attached to the current message" in block
    assert "name=report.txt" in block
    # Live artifact registry block (empty registry still renders its
    # "(none registered yet ...)" state — same as the legacy appendix).
    assert "#### Your registered artifacts (live)" in block
    assert "none registered yet" in block


@pytest.mark.asyncio
async def test_tools_flag_off_is_legacy_byte_identical(flag_off, db_client):
    from xyz_agent_context.module.common_tools_module.common_tools_module import (
        COMMON_TOOLS_INSTRUCTIONS,
    )
    mod = _tools_module(db_client)
    ctx = _attachment_ctx("report.txt")
    out = await mod.get_instructions(ctx)
    expected = "\n\n".join(
        [COMMON_TOOLS_INSTRUCTIONS, *await mod._volatile_sections(ctx)]
    )
    assert out == expected
    assert "#### Files attached to the current message" in out
    assert "#### Your registered artifacts (live)" in out


@pytest.mark.asyncio
async def test_tools_turn_context_fail_open_on_bare_ctx(flag_on):
    # No attachments and no DB (artifact lookup unavailable) → "".
    assert await _tools_module(db=None).get_turn_context(_ctx()) == ""
