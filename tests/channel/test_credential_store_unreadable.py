"""
@file_name: test_credential_store_unreadable.py
@author: Bin Liang
@date: 2026-09-07
@description: One undecryptable credential row (rotated/lost SKILL_SECRETS_KEY, corrupt payload) must not take a whole channel offline: list_active skips it with the error attached to the record, single-record readers surface it, and no read path raises. Removing the per-row guard in GenericCredentialStore._row_to_record turns this red.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec
from narranexus.platform.channel.credential_store import TABLE, GenericCredentialStore

DESC = ChannelDescriptor(
    name="ur_demo",
    display_name="UR",
    transport="webhook",
    credential_schema=CredentialSchema(fields=(CredentialField("api_token", "secret"), CredentialField("workspace", "string", required=False)), supports_test=False),
)


@pytest.fixture(autouse=True)
def _key_dir(tmp_path: Path):
    credential_codec.use_key_dir(tmp_path / "keys")
    yield
    credential_codec.use_key_dir(None)


@pytest.fixture
def regs() -> Registries:
    r = Registries()
    r.registry_for("ingress.channels").register_contribution(Contribution("ur_demo", lambda: DESC), owner="acme.ur")
    return r


@pytest.mark.asyncio
async def test_one_unreadable_row_does_not_break_the_channel(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await store.upsert("ur_demo", "good", {"api_token": "t-good", "workspace": "w"})
    await store.upsert("ur_demo", "bad", {"api_token": "t-bad"})
    # A Fernet-shaped token this install's key cannot open — what a rotated key leaves behind.
    await db_client.update(TABLE, {"channel": "ur_demo", "agent_id": "bad"}, {"secret_json": "gAAAAABnot-a-real-token-for-this-key=="})

    active = await store.list_active("ur_demo")
    assert [r.agent_id for r in active] == ["good"]
    assert active[0].secret == {"api_token": "t-good"} and active[0].readable

    bad = await store.get("ur_demo", "bad")
    assert bad is not None and not bad.readable
    assert bad.secret == {} and "re-bind" in (bad.secret_error or "")
    # channel-wide lookups walk every row and must not raise either
    assert (await store.find_one("ur_demo", workspace="w")).agent_id == "good"
    assert {r.agent_id for r in await store.list_all("ur_demo")} == {"good", "bad"}


@pytest.mark.asyncio
async def test_set_enabled_never_rewrites_an_unreadable_secret(db_client, regs):
    # A rotated key must stay recoverable: flipping the toggle (or the trigger
    # writing disabled_reason) may touch enabled/public_json/version only.
    store = GenericCredentialStore(db_client, regs)
    await store.upsert("ur_demo", "bad", {"api_token": "t-bad", "workspace": "w"})
    stale = "gAAAAABnot-a-real-token-for-this-key=="
    await db_client.update(TABLE, {"channel": "ur_demo", "agent_id": "bad"}, {"secret_json": stale})
    before = await db_client.get_one(TABLE, {"channel": "ur_demo", "agent_id": "bad"})

    assert await store.set_enabled("ur_demo", "bad", False, reason="revoked") is True
    after = await db_client.get_one(TABLE, {"channel": "ur_demo", "agent_id": "bad"})
    assert after["secret_json"] == before["secret_json"] == stale
    assert after["enabled"] == 0 and after["version"] == before["version"] + 1
    record = await store.get("ur_demo", "bad")
    assert record.public == {"workspace": "w", "disabled_reason": "revoked"} and not record.readable

    assert await store.set_enabled("ur_demo", "bad", True) is True
    again = await db_client.get_one(TABLE, {"channel": "ur_demo", "agent_id": "bad"})
    assert again["secret_json"] == stale and again["enabled"] == 1
    assert (await store.get("ur_demo", "bad")).public["disabled_reason"] == ""


@pytest.mark.asyncio
async def test_patch_keeps_an_unreadable_secret_unless_a_new_secret_is_given(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await store.upsert("ur_demo", "bad", {"api_token": "t-bad", "workspace": "w"})
    stale = "gAAAAABnot-a-real-token-for-this-key=="
    await db_client.update(TABLE, {"channel": "ur_demo", "agent_id": "bad"}, {"secret_json": stale})

    # Public-only patch (owner name, auth status, ...): ciphertext untouched.
    assert (await store.patch("ur_demo", "bad", {"workspace": "w2"})).public["workspace"] == "w2"
    assert (await db_client.get_one(TABLE, {"channel": "ur_demo", "agent_id": "bad"}))["secret_json"] == stale

    # A re-bind that carries a new secret is the one thing allowed to replace it.
    fixed = await store.patch("ur_demo", "bad", {"api_token": "t-new"})
    assert fixed.readable and fixed.secret == {"api_token": "t-new"}
    assert (await db_client.get_one(TABLE, {"channel": "ur_demo", "agent_id": "bad"}))["secret_json"] != stale
