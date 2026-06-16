"""
@file_name: test_slot_reasoning_params.py
@author: Bin Liang
@date: 2026-06-10
@description: Framework-neutral reasoning params (thinking / reasoning_effort)
on SlotConfig, persisted through both slot storage backends.

Design (feat/claude-sdk-adapter-upgrade, discussion item 2):
- NarraNexus will adapt more agent frameworks (Codex, pi, ...). The slot
  stores framework-NEUTRAL values; each adapter owns its dialect mapping
  (CLAUDE.md rule #9).
- Two independent knobs, not folded into one, to keep full granularity:
    thinking:         "" (auto) | "on" | "off"
    reasoning_effort: "" (auto) | "low" | "medium" | "high" | "max"
- Storage: user_slots.params_json (single extensible column; future
  per-slot knobs reuse it without another migration).
- Both run modes must behave identically (rule #7): the DB-backed
  UserProviderService (cloud) and the file-backed ProviderRegistry (local)
  accept and round-trip the same fields.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from xyz_agent_context.schema.provider_schema import (
    AuthType,
    LLMConfig,
    ProviderConfig,
    ProviderProtocol,
    ProviderSource,
    SlotConfig,
)


# -------- SlotConfig schema ------------------------------------------------


def test_slot_config_defaults_are_auto():
    """Old persisted configs (no reasoning fields) must parse with auto defaults."""
    cfg = SlotConfig(provider_id="prov_x", model="m")
    assert cfg.thinking == ""
    assert cfg.reasoning_effort == ""


def test_slot_config_accepts_neutral_values():
    cfg = SlotConfig(
        provider_id="prov_x", model="m", thinking="on", reasoning_effort="max"
    )
    assert cfg.thinking == "on"
    assert cfg.reasoning_effort == "max"


@pytest.mark.parametrize("bad", ["adaptive", "disabled", "ON", "true"])
def test_slot_config_rejects_dialect_or_bad_thinking(bad):
    """Claude/OpenAI dialect words must NOT leak into the neutral schema."""
    with pytest.raises(ValidationError):
        SlotConfig(provider_id="prov_x", model="m", thinking=bad)


@pytest.mark.parametrize("bad", ["minimal", "xhigh", "HIGH", "0"])
def test_slot_config_rejects_dialect_or_bad_effort(bad):
    with pytest.raises(ValidationError):
        SlotConfig(provider_id="prov_x", model="m", reasoning_effort=bad)


def test_slot_config_old_json_roundtrip():
    """LLMConfig persisted before this change must still load (local mode file)."""
    old = {
        "version": "1.0",
        "providers": {},
        "slots": {"agent": {"provider_id": "prov_x", "model": "m"}},
    }
    cfg = LLMConfig.model_validate(old)
    assert cfg.slots["agent"].thinking == ""
    assert cfg.slots["agent"].reasoning_effort == ""


# -------- Cloud backend: UserProviderService / user_slots.params_json ------


def _anthropic_provider(provider_id: str = "prov_a1") -> ProviderConfig:
    return ProviderConfig(
        provider_id=provider_id,
        name="Test Anthropic",
        source=ProviderSource.USER,
        protocol=ProviderProtocol.ANTHROPIC,
        auth_type=AuthType.API_KEY,
        api_key="sk-test",
        models=["claude-sonnet-4-6"],
    )


@pytest.fixture
async def svc(db_client):
    """(service, provider_id) with one Anthropic provider for user_1."""
    from xyz_agent_context.agent_framework.user_provider_service import (
        UserProviderService,
    )

    service = UserProviderService(db_client)
    _, new_ids = await service.add_provider(
        "user_1", card_type="anthropic", name="Test Anthropic",
        api_key="sk-test", models=["claude-sonnet-4-6"],
    )
    return service, new_ids[0]


async def test_set_slot_persists_reasoning_params(svc):
    svc, pid = svc
    await svc.set_slot(
        "user_1", "agent", pid, "claude-sonnet-4-6",
        thinking="on", reasoning_effort="high",
    )
    cfg = await svc.get_user_config("user_1")
    slot = cfg.slots["agent"]
    assert slot.thinking == "on"
    assert slot.reasoning_effort == "high"


async def test_set_slot_defaults_to_auto(svc):
    svc, pid = svc
    await svc.set_slot("user_1", "agent", pid, "claude-sonnet-4-6")
    cfg = await svc.get_user_config("user_1")
    assert cfg.slots["agent"].thinking == ""
    assert cfg.slots["agent"].reasoning_effort == ""


async def test_set_slot_overwrite_resets_params(svc):
    """PUT semantics: each set_slot writes the full param set; omitting a
    param on a later call resets it to auto (the UI always sends the
    current dropdown values)."""
    svc, pid = svc
    await svc.set_slot(
        "user_1", "agent", pid, "claude-sonnet-4-6",
        thinking="off", reasoning_effort="low",
    )
    await svc.set_slot("user_1", "agent", pid, "claude-sonnet-4-6")
    cfg = await svc.get_user_config("user_1")
    assert cfg.slots["agent"].thinking == ""
    assert cfg.slots["agent"].reasoning_effort == ""


async def test_legacy_row_without_params_json(svc, db_client):
    """Rows written before the params_json column existed must load as auto."""
    svc, pid = svc
    await svc.set_slot("user_1", "agent", pid, "claude-sonnet-4-6")
    # Simulate a pre-migration row: NULL params_json.
    await db_client.update(
        "user_slots",
        {"user_id": "user_1", "slot_name": "agent"},
        {"params_json": None},
    )
    cfg = await svc.get_user_config("user_1")
    assert cfg.slots["agent"].thinking == ""
    assert cfg.slots["agent"].reasoning_effort == ""


async def test_corrupt_params_json_degrades_to_auto(svc, db_client):
    """A hand-edited / corrupt params_json must not break config loading."""
    svc, pid = svc
    await svc.set_slot("user_1", "agent", pid, "claude-sonnet-4-6")
    await db_client.update(
        "user_slots",
        {"user_id": "user_1", "slot_name": "agent"},
        {"params_json": "{not json"},
    )
    cfg = await svc.get_user_config("user_1")
    assert cfg.slots["agent"].thinking == ""
    assert cfg.slots["agent"].reasoning_effort == ""


# -------- Local backend: ProviderRegistry (llm_config.json) ----------------


def test_registry_set_slot_carries_reasoning_params():
    from xyz_agent_context.agent_framework.provider_registry import ProviderRegistry

    registry = ProviderRegistry()
    config = LLMConfig(providers={"prov_a1": _anthropic_provider()})
    config = registry.set_slot(
        config, "agent", "prov_a1", "claude-sonnet-4-6",
        thinking="on", reasoning_effort="medium",
    )
    slot = config.slots["agent"]
    assert slot.thinking == "on"
    assert slot.reasoning_effort == "medium"
    # And it survives the JSON round-trip that llm_config.json performs.
    reloaded = LLMConfig.model_validate(config.model_dump())
    assert reloaded.slots["agent"].reasoning_effort == "medium"
