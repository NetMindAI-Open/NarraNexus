"""
@file_name: test_generic_credential_store.py
@author: Bin Liang
@date: 2026-09-04
@description: channel_credentials + GenericCredentialStore: values split by the channel schema (secrets encrypted, identity public, external id unique), CRUD/list, and the mirror that copies every bespoke-manager write into the generic table.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec
from narranexus.platform.channel.credential_legacy import LEGACY_BY_TABLE, copy_legacy_tables
from narranexus.platform.channel.credential_store import TABLE, CredentialConflict, GenericCredentialStore, UnknownChannel, missing_required, split_values
from narranexus.kernel.plugins.builtins import load_builtins

PLUGIN = ChannelDescriptor(
    name="acme_chat",
    display_name="Acme Chat",
    transport="webhook",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("bot_token", "secret", required=True),
            CredentialField("bot_id", "string", required=False),
            CredentialField("workspace", "string", required=True),
        ),
        supports_test=False,
        external_id_field="bot_id",
    ),
    has_bind=True,
    has_test=False,
)




@pytest.fixture(autouse=True)
def _key_dir(tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")
    yield
    credential_codec.use_key_dir(None)


@pytest.fixture
def regs() -> Registries:
    r = Registries()
    load_builtins(r, "backend")
    reg = r.registry_for("ingress.channels")
    reg.register_contribution(Contribution("acme_chat", lambda: PLUGIN), owner="acme.chat")
    return r


def test_split_by_schema_and_secret_heuristics():
    public, secret, external = split_values(PLUGIN, {"bot_token": "t", "bot_id": "b1", "workspace": "w", "refresh_token": "r", "agent_id": "ignored"})
    assert public == {"bot_id": "b1", "workspace": "w"} and secret == {"bot_token": "t", "refresh_token": "r"} and external == "b1"
    assert missing_required(PLUGIN, {"bot_token": "t"}) == ("workspace",)
    assert missing_required(PLUGIN, {"bot_token": "t", "workspace": "w"}) == ()


def test_codec_roundtrip_is_encrypted():
    blob = credential_codec.encode_secrets({"bot_token": "top-secret"})
    assert "top-secret" not in blob and credential_codec.decode_secrets(blob) == {"bot_token": "top-secret"}
    assert credential_codec.encode_secrets({}) == "" and credential_codec.decode_secrets("") == {}


@pytest.mark.asyncio
async def test_store_crud_and_secret_at_rest(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    rec = await store.upsert("acme_chat", "a1", {"bot_token": "tok", "bot_id": "b1", "workspace": "w"})
    assert rec.to_public_dict() == {"channel": "acme_chat", "agent_id": "a1", "enabled": True, "external_id": "b1", "bot_id": "b1", "workspace": "w"}
    assert rec.to_raw_dict()["bot_token"] == "tok"
    row = await db_client.get_one(TABLE, {"channel": "acme_chat", "agent_id": "a1"})
    assert "tok" not in row["secret_json"] and "tok" not in row["public_json"]
    assert (await store.get_public("acme_chat", "a1"))["workspace"] == "w"
    assert await store.set_enabled("acme_chat", "a1", False) and not (await store.get("acme_chat", "a1")).enabled
    assert [r.agent_id for r in await store.list_active("acme_chat")] == []
    assert await store.update("acme_chat", "a1", {"workspace": "w2"})
    assert (await store.get("acme_chat", "a1")).public["workspace"] == "w2" and (await store.get("acme_chat", "a1")).secret["bot_token"] == "tok"
    assert await store.unbind("acme_chat", "a1") and not await store.unbind("acme_chat", "a1")
    with pytest.raises(UnknownChannel):
        await store.upsert("nope", "a1", {})


@pytest.mark.asyncio
async def test_external_id_is_unique_per_channel(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await store.upsert("acme_chat", "a1", {"bot_token": "t", "bot_id": "same", "workspace": "w"})
    with pytest.raises(CredentialConflict, match="already bound to another agent"):
        await store.upsert("acme_chat", "a2", {"bot_token": "t", "bot_id": "same", "workspace": "w"})
    # the losing row must not have been written — a broad `except Exception`
    # (any unrelated failure: a missing column, a changed signature raising
    # TypeError) would satisfy `pytest.raises(Exception)` while silently
    # leaving a2's row committed if the guard were ever relaxed.
    assert await store.get("acme_chat", "a2") is None


@pytest.mark.asyncio
async def test_legacy_tables_are_copied_once_with_secrets_decoded(db_client, regs):
    import base64

    await db_client.insert("channel_telegram_credentials", {"agent_id": "a1", "bot_token_encoded": base64.b64encode(b"tg-token").decode(), "bot_user_id": "42", "bot_username": "bot", "enabled": 1})
    await db_client.insert("channel_slack_credentials", {"agent_id": "a2", "bot_token_encoded": base64.b64encode(b"xoxb").decode(), "app_token_encoded": base64.b64encode(b"xapp").decode(), "bot_user_id": "U1", "team_id": "T1", "enabled": 0})
    await db_client.insert("lark_credentials", {"agent_id": "a3", "app_id": "cli_1", "app_secret_ref": "r", "app_secret_encrypted": base64.b64encode(b"sec").decode(), "brand": "feishu", "profile_name": "p", "auth_status": "bot_ready", "is_active": 1, "permission_state": '{"admin_request_url": "u"}'})
    counts = await copy_legacy_tables(db_client)
    assert counts["telegram"] == 1 and counts["slack"] == 1 and counts["lark"] == 1 and counts["wechat"] == 0
    store = GenericCredentialStore(db_client, regs)
    tg = await store.get("telegram", "a1")
    assert tg.secret == {"bot_token": "tg-token"} and tg.external_id == "42" and tg.enabled
    sl = await store.get("slack", "a2")
    assert sl.secret == {"bot_token": "xoxb", "app_token": "xapp"} and not sl.enabled and sl.public["team_id"] == "T1"
    lk = await store.get("lark", "a3")
    assert lk.secret["app_secret_encoded"] == base64.b64encode(b"sec").decode() and lk.public["permission_state"] == {"admin_request_url": "u"} and lk.external_id == "cli_1"
    # idempotent: a second run copies nothing and leaves the generic rows alone
    await store.patch("telegram", "a1", {"bot_username": "renamed"})
    assert await copy_legacy_tables(db_client) == {"telegram": 0, "slack": 0, "discord": 0, "wechat": 0, "narramessenger": 0, "lark": 0}
    assert (await store.get("telegram", "a1")).public["bot_username"] == "renamed"
    assert set(LEGACY_BY_TABLE) == {"channel_telegram_credentials", "channel_slack_credentials", "channel_discord_credentials", "channel_wechat_credentials", "channel_narramessenger_credentials", "lark_credentials"}


def test_migration_registered():
    from backend.migrations import REGISTRY

    assert [m.id for m in REGISTRY[-2:]] == ["0004_channel_credentials_backfill", "0005_channel_credentials_switch"]
