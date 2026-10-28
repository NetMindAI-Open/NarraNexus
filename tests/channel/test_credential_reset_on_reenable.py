"""
@file_name: test_credential_reset_on_reenable.py
@date: 2026-10-14
@description: ``CredentialSchema.reset_on_reenable`` — per-channel stream state
that must not survive a disabled period.

A binding that was switched off and later on must start its transport fresh:
a resumed Matrix /sync cursor would deliver everything sent while it was off
and the agent would answer days-old messages. Rows reach "disabled" through
many doors (the set-active route, ``disable_credential``, a bundle import that
lands every binding inactive, lark's own ``patch(enabled=False)``, rows that
were already disabled before the declaration existed) so the reset is applied
at the one place every route back to enabled passes: the DISABLED -> ENABLED
transition inside ``GenericCredentialStore`` (``set_enabled(True)``,
``patch(enabled=True)``, ``upsert(enabled=True)`` onto a disabled row).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.channel import credential_codec
from narranexus.platform.channel.credential_store import GenericCredentialStore, descriptor_for


def _descriptor(name: str, reset: tuple[str, ...]) -> ChannelDescriptor:
    return ChannelDescriptor(
        name=name,
        display_name=name,
        transport="webhook",
        credential_schema=CredentialSchema(
            fields=(
                CredentialField("bot_token", "secret", required=True),
                CredentialField("bot_id", "string", required=False),
            ),
            supports_test=False,
            external_id_field="bot_id",
            reset_on_reenable=reset,
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
    reg.register_contribution(Contribution("cursor_chat", lambda: _descriptor("cursor_chat", ("cursor", "sync_token"))), owner="t.a")
    reg.register_contribution(Contribution("plain_chat", lambda: _descriptor("plain_chat", ())), owner="t.b")
    return r


async def _bind(store: GenericCredentialStore, channel: str, *, enabled: bool) -> None:
    await store.upsert(channel, "a1", {"bot_token": "t", "bot_id": "b1", "cursor": "s42", "sync_token": "tok42", "note": "keep"}, enabled=enabled)


async def _public(store: GenericCredentialStore, channel: str) -> dict:
    return (await store.get(channel, "a1")).public


@pytest.mark.asyncio
async def test_set_enabled_true_on_a_disabled_row_blanks_the_declared_fields_only(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=False)  # e.g. a bundle import / a legacy disabled row
    assert await store.set_enabled("cursor_chat", "a1", True)
    pub = await _public(store, "cursor_chat")
    assert pub["cursor"] == "" and pub["note"] == "keep"
    secret = (await store.get("cursor_chat", "a1")).secret
    # the real narramessenger cursor is stored with the secrets (secret-like name): both halves reset
    assert secret["sync_token"] == "" and secret["bot_token"] == "t"


@pytest.mark.asyncio
async def test_patch_enabled_true_on_a_disabled_row_blanks_in_the_same_write(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=False)
    before = (await store.get("cursor_chat", "a1")).version
    await store.patch("cursor_chat", "a1", {"note": "n2"}, enabled=True)  # lark-style flip
    rec = await store.get("cursor_chat", "a1")
    assert rec.enabled and rec.public["cursor"] == "" and rec.public["note"] == "n2"
    assert rec.version == before + 1  # one versioned write, not two


@pytest.mark.asyncio
async def test_upsert_enabled_true_over_a_disabled_row_blanks(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=False)
    await _bind(store, "cursor_chat", enabled=True)  # carries cursor="s42" again
    pub = await _public(store, "cursor_chat")
    assert (await store.get("cursor_chat", "a1")).enabled
    assert pub["cursor"] == ""


@pytest.mark.asyncio
async def test_disabling_itself_keeps_the_values_until_re_enable(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=True)
    await store.set_enabled("cursor_chat", "a1", False, reason="owner")
    assert (await _public(store, "cursor_chat"))["cursor"] == "s42"


@pytest.mark.asyncio
async def test_a_late_write_onto_a_disabled_row_is_cleared_at_re_enable(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=True)
    await store.set_enabled("cursor_chat", "a1", False)
    await store.patch("cursor_chat", "a1", {"cursor": "s_late"})  # subscriber's last sync lands after the toggle
    await store.set_enabled("cursor_chat", "a1", True)
    assert (await _public(store, "cursor_chat"))["cursor"] == ""


@pytest.mark.asyncio
async def test_rows_that_stay_enabled_and_fresh_binds_are_untouched(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=True)  # fresh bind keeps what the caller passed
    assert (await _public(store, "cursor_chat"))["cursor"] == "s42"
    await store.patch("cursor_chat", "a1", {"cursor": "s43"})
    assert await store.set_enabled("cursor_chat", "a1", True)  # already enabled: no transition
    await store.patch("cursor_chat", "a1", {"note": "x"}, enabled=True)
    assert (await _public(store, "cursor_chat"))["cursor"] == "s43"
    await _bind(store, "cursor_chat", enabled=True)  # rebind of an enabled row
    assert (await _public(store, "cursor_chat"))["cursor"] == "s42"


@pytest.mark.asyncio
async def test_a_row_that_stays_disabled_is_untouched(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "cursor_chat", enabled=False)
    await store.patch("cursor_chat", "a1", {"note": "n"})
    await store.upsert("cursor_chat", "a1", {"bot_token": "t", "bot_id": "b1", "cursor": "s50"})  # enabled=None
    rec = await store.get("cursor_chat", "a1")
    assert not rec.enabled and rec.public["cursor"] == "s50"


@pytest.mark.asyncio
async def test_a_channel_without_the_declaration_keeps_everything(db_client, regs):
    store = GenericCredentialStore(db_client, regs)
    await _bind(store, "plain_chat", enabled=False)
    await store.set_enabled("plain_chat", "a1", True)
    await store.patch("plain_chat", "a1", {"note": "n"}, enabled=True)
    assert (await _public(store, "plain_chat"))["cursor"] == "s42"


def test_declarations_of_the_builtin_channels():
    assert descriptor_for("narramessenger").credential_schema.reset_on_reenable == ("matrix_since_token",)
    # lark flips enabled through patch(enabled=...) itself; it declares nothing, so it is unaffected
    assert descriptor_for("lark").credential_schema.reset_on_reenable == ()


# ── an unreadable secret blob must never be rewritten by the reset ──

STALE_BLOB = "gAAAAABnot-a-real-token-for-this-key=="


async def _make_unreadable(db_client, store, channel: str, *, enabled: bool) -> dict:
    from narranexus.platform.channel.credential_store import TABLE

    await _bind(store, channel, enabled=enabled)
    # What a rotated / lost key leaves behind: a ciphertext this install cannot open.
    await db_client.update(TABLE, {"channel": channel, "agent_id": "a1"}, {"secret_json": STALE_BLOB})
    return await db_client.get_one(TABLE, {"channel": channel, "agent_id": "a1"})


@pytest.mark.asyncio
@pytest.mark.parametrize("flip", ["set_enabled", "patch"])
async def test_re_enable_never_rewrites_an_unreadable_secret_blob(db_client, regs, flip):
    """Regression: the reset adds a secret-side value, which made the split
    ``secret`` non-empty and defeated patch()'s fail-closed guard — the original
    ciphertext (incl. the access token) was overwritten, so a binding that would
    self-heal once the key is restored needed a rebind."""
    from narranexus.platform.channel.credential_store import TABLE

    store = GenericCredentialStore(db_client, regs)
    before = await _make_unreadable(db_client, store, "cursor_chat", enabled=False)
    if flip == "set_enabled":
        assert await store.set_enabled("cursor_chat", "a1", True)
    else:
        await store.patch("cursor_chat", "a1", {"note": "n2"}, enabled=True)
    after = await db_client.get_one(TABLE, {"channel": "cursor_chat", "agent_id": "a1"})
    assert after["secret_json"] == before["secret_json"] == STALE_BLOB
    assert after["enabled"] == 1 and after["version"] == before["version"] + 1
    # the public side of the declaration is still blanked; the secret side cannot be
    # touched while unreadable (such a row is skipped by list_active, so it cannot connect)
    assert (await store.get("cursor_chat", "a1")).public["cursor"] == ""


@pytest.mark.asyncio
async def test_narramessenger_unreadable_row_keeps_its_ciphertext_on_enable(db_client):
    from narranexus.platform.channel.credential_store import TABLE

    store = GenericCredentialStore(db_client)
    await store.upsert("narramessenger", "a1", {
        "matrix_homeserver_url": "https://h", "matrix_user_id": "@agent-x:h",
        "matrix_access_token": "at", "matrix_since_token": "s42",
    }, enabled=False)
    await db_client.update(TABLE, {"channel": "narramessenger", "agent_id": "a1"}, {"secret_json": STALE_BLOB})
    assert await store.set_enabled("narramessenger", "a1", True)
    row = await db_client.get_one(TABLE, {"channel": "narramessenger", "agent_id": "a1"})
    assert row["secret_json"] == STALE_BLOB


@pytest.mark.asyncio
async def test_upsert_over_an_unreadable_disabled_row_is_a_rebind_and_still_resets(db_client, regs):
    """upsert replaces the whole row with caller-supplied values (a rebind); that
    path never had the fail-closed guard and keeps its behaviour."""
    store = GenericCredentialStore(db_client, regs)
    await _make_unreadable(db_client, store, "cursor_chat", enabled=False)
    await _bind(store, "cursor_chat", enabled=True)
    rec = await store.get("cursor_chat", "a1")
    assert rec.readable and rec.enabled
    assert rec.public["cursor"] == "" and rec.secret["sync_token"] == "" and rec.secret["bot_token"] == "t"
