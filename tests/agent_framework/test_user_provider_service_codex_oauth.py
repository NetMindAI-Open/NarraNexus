"""
@file_name: test_user_provider_service_codex_oauth.py
@date: 2026-05-30
@description: Tests for the codex_oauth branch of
UserProviderService.add_provider — symmetric to the claude_oauth
behaviour, lets users "Add as Provider" from the Settings page so
the Codex OAuth credential becomes assignable to the agent slot.
"""
from __future__ import annotations

import json

import pytest

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.provider_driver import (
    resolve_user_runtime_llm_configs,
)
from xyz_agent_context.agent_framework.provider_driver.backfill import (
    backfill_provider_metadata,
)
from xyz_agent_context.agent_framework.user_provider_service import (
    CODEX_CURATED_MODELS,
    UserProviderService,
)


class _FakeDB:
    """Tiny in-memory user_providers stand-in keyed by provider_id."""

    def __init__(self):
        self.providers: dict[str, dict] = {}  # provider_id -> row
        self.slots: dict[tuple, dict] = {}    # (user_id, slot_name) -> row

    async def get(self, table, filters=None):
        if table == "user_providers":
            filters = filters or {}
            return [
                r for r in self.providers.values()
                if all(r.get(k) == v for k, v in filters.items())
            ]
        if table == "user_slots":
            filters = filters or {}
            return [
                r for r in self.slots.values()
                if all(r.get(k) == v for k, v in filters.items())
            ]
        return []

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        if table == "user_providers":
            self.providers[data["provider_id"]] = dict(data)
        elif table == "user_slots":
            key = (data["user_id"], data["slot_name"])
            self.slots[key] = dict(data)

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        return 0


@pytest.mark.asyncio
async def test_add_codex_oauth_inserts_one_provider_row():
    db = _FakeDB()
    svc = UserProviderService(db)
    config, new_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    assert len(new_ids) == 1
    row = db.providers[new_ids[0]]
    assert row["source"] == "codex_oauth"
    assert row["protocol"] == "openai"
    assert row["auth_type"] == "oauth"
    assert row["api_key"] == ""           # OAuth → no env-key
    assert row["base_url"] == ""           # Codex picks default endpoint
    assert not row["supports_anthropic_server_tools"]  # stored as 0/False
    assert row["name"] == "Codex CLI (OAuth)"
    assert row["driver_type"] == "codex_oauth"
    assert row["auth_ref"] == "codex-cli:~/.codex/auth.json"


@pytest.mark.asyncio
async def test_add_codex_oauth_rejects_duplicate():
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.add_provider(user_id="u1", card_type="codex_oauth")
    with pytest.raises(ValueError, match="already exists"):
        await svc.add_provider(user_id="u1", card_type="codex_oauth")


@pytest.mark.asyncio
async def test_add_codex_oauth_for_different_user_succeeds():
    """Per-user constraint — user u1's codex_oauth doesn't block u2's."""
    db = _FakeDB()
    svc = UserProviderService(db)
    await svc.add_provider(user_id="u1", card_type="codex_oauth")
    config, new_ids = await svc.add_provider(user_id="u2", card_type="codex_oauth")
    assert len(new_ids) == 1


@pytest.mark.asyncio
async def test_add_codex_oauth_protocol_is_openai_not_anthropic():
    """codex_oauth row's protocol is OpenAI (Codex uses OpenAI's
    Responses API surface). This matters for slot eligibility — the
    helper_llm / embedding slots use protocol filtering."""
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    row = db.providers[new_ids[0]]
    assert row["protocol"] == "openai"
    assert row["protocol"] != "anthropic"


@pytest.mark.asyncio
async def test_codex_agent_framework_allows_openai_provider_for_agent_slot():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    await svc.set_user_agent_framework("u1", "codex_cli")

    config = await svc.set_slot("u1", "agent", new_ids[0], "gpt-5.4-codex")

    assert config.slots["agent"].provider_id == new_ids[0]
    assert db.slots[("u1", "agent")]["agent_framework"] == "codex_cli"


@pytest.mark.asyncio
async def test_claude_agent_framework_rejects_openai_provider_for_agent_slot():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, new_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    # Adding codex_oauth auto-sets framework=codex_cli (subscription covers
    # both slots). Force claude_code back to exercise the protocol guard:
    # a claude_code agent slot must reject an openai-protocol provider.
    await svc.set_user_agent_framework("u1", "claude_code")

    with pytest.raises(ValueError, match="requires protocol \\['anthropic'\\]"):
        await svc.set_slot("u1", "agent", new_ids[0], "gpt-5.4-codex")


@pytest.mark.asyncio
async def test_runtime_resolver_builds_codex_config_for_codex_agent_slot():
    db = _FakeDB()
    svc = UserProviderService(db)
    _, codex_ids = await svc.add_provider(user_id="u1", card_type="codex_oauth")
    # Simulate a stale row created before codex_oauth wrote its own auth_ref.
    db.providers[codex_ids[0]]["auth_ref"] = "claude-cli:~/.claude/.credentials.json"
    await svc.set_user_agent_framework("u1", "codex_cli")
    await svc.set_slot("u1", "agent", codex_ids[0], "gpt-5.4-codex")

    await db.insert("user_providers", {
        "user_id": "u1",
        "provider_id": "prov_openai",
        "name": "OpenAI",
        "source": "user",
        "protocol": "openai",
        "auth_type": "api_key",
        "api_key": "sk-test",
        "base_url": "https://api.openai.com/v1",
        "models": json.dumps(["gpt-test", "text-embedding-3-small"]),
        "linked_group": "",
        "is_active": 1,
        "driver_type": "custom_openai",
    })
    await db.insert("user_slots", {
        "user_id": "u1",
        "slot_name": "helper_llm",
        "provider_id": "prov_openai",
        "model": "gpt-test",
    })
    await db.insert("user_slots", {
        "user_id": "u1",
        "slot_name": "embedding",
        "provider_id": "prov_openai",
        "model": "text-embedding-3-small",
    })

    cfg = await resolve_user_runtime_llm_configs("u1", db)

    assert cfg.claude == ClaudeConfig()
    # The slot stored ``gpt-5.4-codex`` (a fake model name from the
    # pre-2026-06-04 era — see the design doc's "what we got wrong"
    # notes), but ``self_heal_if_broken`` repairs any value not in
    # the codex_oauth provider's ``models`` list to the first entry
    # of ``CODEX_CURATED_MODELS``. Binding the expectation to the
    # constant means future curated-list bumps don't break this
    # test.
    assert cfg.codex.model == CODEX_CURATED_MODELS[0]
    assert cfg.codex.auth_type == "oauth"
    assert cfg.codex.auth_ref == "codex-cli:~/.codex/auth.json"
    assert cfg.openai.api_key == "sk-test"


@pytest.mark.asyncio
async def test_backfill_normalizes_stale_codex_oauth_auth_ref():
    db = _FakeDB()
    await db.insert("user_providers", {
        "user_id": "u1",
        "provider_id": "prov_codex",
        "name": "Codex CLI (OAuth)",
        "source": "codex_oauth",
        "protocol": "openai",
        "auth_type": "oauth",
        "api_key": "",
        "base_url": "",
        "models": json.dumps(["gpt-5.4-codex"]),
        "linked_group": "",
        "is_active": 1,
        "driver_type": "codex_oauth",
        "billing_policy": "external_oauth",
        "auth_ref": "claude-cli:~/.claude/.credentials.json",
        "owner_user_id": "u1",
    })

    stats = await backfill_provider_metadata(db)

    assert db.providers["prov_codex"]["auth_ref"] == "codex-cli:~/.codex/auth.json"
    assert stats["classified"] == 0
    assert stats["normalized_auth_refs"] == 1

    second_stats = await backfill_provider_metadata(db)
    assert second_stats["normalized_auth_refs"] == 0
