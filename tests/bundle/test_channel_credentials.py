"""
@file_name: test_channel_credentials.py
@author: NetMind.AI
@date: 2026-07-10
@description: Opt-in channel-credential export/import roundtrip tests.

Covers the "带凭据打包" feature:

1. Default export does NOT ship any IM channel credential (privacy — the
   pre-feature behaviour is unchanged when the opt-in flag is off).
2. With include_channel_credentials=True, the six credential tables ride
   along and manifest records contains_channel_credentials=True.
3. Import FORCES the credential inactive (is_active/enabled=0) regardless of
   the source value — the core anti-double-connect invariant. The user must
   manually activate in the new environment (which claims the single WS slot).
4. Import PRESERVES the IM-side owner identity (owner_user_id / user_id on
   slack/telegram/wechat/discord are IM-namespace, NOT NarraNexus user ids —
   they must not be reattributed to the recipient).
5. A bot-identity clash (same Lark profile / Slack bot already bound in the
   target env) is SKIPPED, not force-overwritten, and reported in the summary.

Uses the same get_db_client-wired fixtures as test_roundtrip.py (build_bundle /
importer reach the DB via get_db_client(), so the test DB must be the same one).
"""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))


# ---- fixtures (mirror test_roundtrip.py: wire get_db_client to a test DB) ----

@pytest.fixture
def tmp_db_path(tmp_path):
    return tmp_path / "test_nexus.db"


@pytest.fixture
def tmp_workspace_root(tmp_path, monkeypatch):
    ws = tmp_path / "workspaces"
    ws.mkdir()
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    from narranexus.platform.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    from narranexus.platform.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")

    from narranexus.platform.utils.db import db_factory
    db_factory._clients_by_loop.clear()

    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils.db.schema_registry import auto_migrate

    db = await get_db_client()
    await auto_migrate(db._backend)
    yield db
    db_factory._clients_by_loop.clear()


# ---- seed helpers ----

async def _seed_agent(db, agent_id: str, agent_name: str, user_id: str = "test_user"):
    if not await db.get_one("users", {"user_id": user_id}):
        await db.insert("users", {
            "user_id": user_id,
            "user_type": "local",
            "role": "user",
            "display_name": "Test User",
        })
    await db.insert("agents", {
        "agent_id": agent_id,
        "agent_name": agent_name,
        "created_by": user_id,
        "agent_description": f"Description of {agent_name}",
        "agent_type": "default",
    })


async def _seed_lark_cred(db, agent_id: str, profile_name: str, is_active: int = 1,
                          app_id: str | None = None, workspace_path: str = ""):
    from narranexus_plugins.lark_module._lark_credential_manager import (
        LarkCredential, LarkCredentialManager, _encode_secret,
    )

    await LarkCredentialManager(db).save_credential(LarkCredential(
        agent_id=agent_id, app_id=app_id or f"cli_{agent_id}", app_secret_ref="ref_xxx",
        app_secret_encoded=_encode_secret("secret"), brand="lark", profile_name=profile_name,
        auth_status="bot_ready", is_active=bool(is_active), workspace_path=workspace_path,
    ))


async def _seed_wechat_cred(db, agent_id: str, owner_user_id: str, enabled: int = 1):
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    await GenericCredentialStore(db).upsert("wechat", agent_id, {
        # bot_wx_id (the channel-wide identity) is empty: WeChat learns it on the
        # first DM, and a same-DB roundtrip of an identified bot is a clash by design.
        "bot_token": "token", "base_url": "http://localhost:9000", "bot_wx_id": "",
        "owner_wx_id": "wxid_owner", "owner_user_id": owner_user_id,  # IM-side id — must survive import
        "owner_name": "WX Owner",
    }, enabled=bool(enabled))


async def _seed_slack_cred(db, agent_id: str, team_id: str, bot_user_id: str,
                           owner_user_id: str, enabled: int = 1):
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    await GenericCredentialStore(db).upsert("slack", agent_id, {
        "bot_token": "xoxb-token", "app_token": "xapp-token", "bot_user_id": bot_user_id,
        "team_id": team_id, "team_name": "Test WS", "owner_user_id": owner_user_id,  # Slack user id — must survive import
    }, enabled=bool(enabled))


async def _rows(db, channel: str):
    """Generic credential rows for one channel (public fields decoded for assertions)."""
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    return await GenericCredentialStore(db).list_all(channel)


def _read_member(bundle_path: Path, member: str):
    with zipfile.ZipFile(bundle_path) as z:
        names = z.namelist()
        if member not in names:
            return None
        return z.read(member).decode("utf-8")


# ---- tests ----

async def test_default_export_excludes_credentials(db_client, tmp_workspace_root, tmp_path):
    """Opt-in flag off (default) → no credential file, manifest flag False."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_cred0001", "test_user"
    await _seed_agent(db_client, aid, "Creddy", uid)
    await _seed_lark_cred(db_client, aid, "prof_default")

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(uid, ExportSelection(agent_ids=[aid]), bundle)

    assert _read_member(bundle, f"agents/{aid}/channel_credentials.json") is None
    manifest = json.loads(_read_member(bundle, "manifest.json"))
    assert manifest.get("contains_channel_credentials") is False
    # Not opted in → the preview correctly lists channel credentials as stripped.
    assert "im_channel_credentials" in manifest.get("stripped", [])


async def test_optin_export_includes_credentials(db_client, tmp_workspace_root, tmp_path):
    """Opt-in flag on → the lark row ships under its table key; manifest True."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle

    aid, uid = "agent_cred0002", "test_user"
    await _seed_agent(db_client, aid, "Creddy2", uid)
    await _seed_lark_cred(db_client, aid, "prof_optin")

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle
    )

    raw = _read_member(bundle, f"agents/{aid}/channel_credentials.json")
    assert raw is not None
    creds = json.loads(raw)
    assert list(creds) == ["channel_credentials"]
    (row,) = creds["channel_credentials"]
    assert row["channel"] == "lark" and row["profile_name"] == "prof_optin" and row["agent_id"] == aid
    # secrets travel decoded inside the opt-in file (the file itself is the trust boundary)
    assert row["app_secret_encoded"]

    manifest = json.loads(_read_member(bundle, "manifest.json"))
    assert manifest.get("contains_channel_credentials") is True
    # Preview must NOT claim channel credentials were stripped when they shipped.
    assert "im_channel_credentials" not in manifest.get("stripped", [])
    assert "user_providers" in manifest.get("stripped", [])  # providers never travel


async def test_provider_secrets_are_absent_from_the_archive_itself(db_client, tmp_workspace_root, tmp_path):
    """Assert on the ARCHIVE, not on the manifest's self-description.

    ``stripped_lists`` in builder.py is a hand-written literal with no derivation
    from ``STRIPPED_TABLES``: dropping ``user_providers`` from the sensitive-table
    set while leaving the literal alone ships every provider API key inside the
    bundle AND has the manifest report that it was stripped — the worst of both,
    and green under an assertion that only reads the manifest. So: write a real
    row with a distinctive secret, export with credentials opted IN (the widest
    export the product offers), then search every member of the zip for it.
    """
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle, STRIPPED_TABLES

    aid, uid = "agent_cred0009", "test_user"
    secret = "sk-do-not-ship-me-4d5e6f"
    await _seed_agent(db_client, aid, "Creddy9", uid)
    await _seed_lark_cred(db_client, aid, "prof_scrub")
    await db_client.insert("user_providers", {
        "provider_id": "prov_scrub", "user_id": uid, "name": "scrub-me", "source": "custom",
        "protocol": "anthropic", "auth_type": "api_key", "api_key": secret,
        "base_url": "https://scrub.example", "is_active": 1,
    })
    assert (await db_client.get_one("user_providers", {"provider_id": "prov_scrub"}))["api_key"] == secret

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle)

    with zipfile.ZipFile(bundle) as z:
        members = z.namelist()
        assert members, "empty bundle: the scan below would prove nothing"
        offenders = {name for name in members if secret.encode() in z.read(name)}
    assert offenders == set(), offenders
    assert not any("user_providers" in name for name in members), members

    # ... and the manifest's claim is derived from the same set the export honours.
    manifest = json.loads(_read_member(bundle, "manifest.json"))
    assert "user_providers" in manifest.get("stripped", [])
    assert "user_providers" in STRIPPED_TABLES


async def test_import_forces_inactive_and_remaps_agent(db_client, tmp_workspace_root, tmp_path):
    """Imported credential lands with enabled=0 (even though source=1) and its
    agent_id is remapped to the freshly-minted agent."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    aid, uid = "agent_cred0003", "test_user"
    await _seed_agent(db_client, aid, "Creddy3", uid)
    await _seed_wechat_cred(db_client, aid, owner_user_id="wx_owner_ABC", enabled=1)

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle
    )
    pre = await preflight(bundle, uid)
    summary = await confirm(pre["preflight_token"], uid)

    rows = await _rows(db_client, "wechat")
    # original (enabled, aid) + imported (disabled, new aid)
    assert len(rows) == 2
    imported = [r for r in rows if r.agent_id != aid]
    assert len(imported) == 1
    assert imported[0].enabled is False
    assert imported[0].secret["bot_token"] == "token"
    # new agent_id must be a real, freshly-minted agent in this DB
    assert imported[0].agent_id.startswith("agent_")
    assert await db_client.get_one("agents", {"agent_id": imported[0].agent_id})
    assert summary.get("channel_credentials_imported", 0) == 1


async def test_import_preserves_im_owner_identity(db_client, tmp_workspace_root, tmp_path):
    """owner_user_id is an IM-side id and must NOT be reattributed to the
    recipient NarraNexus user_id by the generic user-attribution rewrite."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    aid, uid = "agent_cred0004", "test_user"
    await _seed_agent(db_client, aid, "Creddy4", uid)
    await _seed_wechat_cred(db_client, aid, owner_user_id="wx_owner_XYZ", enabled=1)

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle
    )
    pre = await preflight(bundle, uid)
    await confirm(pre["preflight_token"], uid)

    rows = await _rows(db_client, "wechat")
    imported = [r for r in rows if r.agent_id != aid][0]
    assert imported.public["owner_user_id"] == "wx_owner_XYZ"


async def test_credential_clash_is_skipped(db_client, tmp_workspace_root, tmp_path):
    """Same-DB roundtrip: the imported Slack credential collides with the
    source's own (team_id, bot_user_id) binding → skipped, not overwritten."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    aid, uid = "agent_cred0005", "test_user"
    await _seed_agent(db_client, aid, "Creddy5", uid)
    await _seed_slack_cred(db_client, aid, team_id="T_ACME", bot_user_id="U_BOT",
                           owner_user_id="U_SLACK_OWNER", enabled=1)

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle
    )
    pre = await preflight(bundle, uid)
    # preflight surfaces the clash for the UI to warn on
    assert pre.get("credential_clashes")
    summary = await confirm(pre["preflight_token"], uid)

    # Still exactly one slack cred (the original) — the clashing import skipped.
    rows = await _rows(db_client, "slack")
    assert len(rows) == 1
    assert rows[0].agent_id == aid
    assert summary.get("channel_credentials_skipped_conflict", 0) == 1


async def test_lark_clash_keys_on_app_id_not_profile_name(db_client, tmp_workspace_root, tmp_path):
    """Lark's bot identity is app_id, NOT the agent-derived profile_name. A bundle
    carrying app_id X must be detected as a clash when X is already bound in the
    target env under a DIFFERENT profile_name — the exact case profile_name missed.
    """
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    SHARED_APP = "cli_shared_app"
    src, uid = "agent_larksrc01", "test_user"
    await _seed_agent(db_client, src, "Lark Src", uid)
    await _seed_lark_cred(db_client, src, profile_name="prof_src", app_id=SHARED_APP)

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[src], include_channel_credentials=True), bundle
    )

    # Free prof_src so ONLY app_id (not profile_name) can match in the target env,
    # then bind the same Lark app under a different agent + profile.
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    await GenericCredentialStore(db_client).unbind("lark", src)
    other = "agent_larkother1"
    await _seed_agent(db_client, other, "Lark Other", uid)
    await _seed_lark_cred(db_client, other, profile_name="prof_other", app_id=SHARED_APP)

    pre = await preflight(bundle, uid)
    assert pre.get("credential_clashes")  # app_id match → surfaced
    summary = await confirm(pre["preflight_token"], uid)

    # The same Lark app is not double-bound: only the existing (other) row remains.
    rows = await _rows(db_client, "lark")
    assert [r.agent_id for r in rows] == [other]
    assert summary.get("channel_credentials_skipped_conflict", 0) == 1


async def test_export_scrubs_the_owner_user_id_and_import_rewrites_it(db_client, tmp_workspace_root, tmp_path):
    """The exporter's NarraNexus user id must not travel in a shareable bundle
    (lark's workspace_path is `<user_id>/<agent_id>`); it is replaced by the
    `<original_owner>` placeholder on export and re-attributed to the RECIPIENT
    on import, so the imported binding never points at the previous owner's
    per-user workspace."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    aid, uid, recipient = "agent_aaaa0009cccc", "test_user", "recipient_user"
    await _seed_agent(db_client, aid, "Creddy9", uid)
    await _seed_lark_cred(db_client, aid, "prof_scrub", workspace_path=f"{uid}/{aid}")

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle)
    raw = _read_member(bundle, f"agents/{aid}/channel_credentials.json")
    (row,) = json.loads(raw)["channel_credentials"]
    assert row["workspace_path"] == f"<original_owner>/{aid}"
    assert uid not in raw, "the exporter's user id leaked into the credential file"
    # the IM-side identity and the agent id (remapped on import) are untouched by the scrub
    assert row["app_id"] == f"cli_{aid}" and row["agent_id"] == aid

    # A different install: the exporting binding is not here, so the same
    # app_id is not a clash (same-install imports skip it fail-closed).
    await db_client.delete("channel_credentials", {"agent_id": aid})
    pre = await preflight(bundle, recipient)
    await confirm(pre["preflight_token"], recipient)
    imported = [r for r in await _rows(db_client, "lark") if r.agent_id != aid]
    assert len(imported) == 1
    new_aid = imported[0].agent_id
    assert imported[0].public["workspace_path"] == f"{recipient}/{new_aid}"


async def test_legacy_per_table_bundle_still_imports(db_client, tmp_workspace_root, tmp_path):
    """A bundle exported before 4d carries ``{"<legacy table>": [rows]}``; the
    importer normalizes it through the same legacy column map and lands the
    credential in the generic store, inactive."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm

    aid, uid = "agent_cred0006", "test_user"
    await _seed_agent(db_client, aid, "Creddy6", uid)
    bundle = tmp_path / "b.nxbundle"
    await build_bundle(uid, ExportSelection(agent_ids=[aid]), bundle)
    legacy = {"channel_telegram_credentials": [{
        "agent_id": aid, "bot_token_encoded": "dGctdG9rZW4=", "bot_user_id": "4242", "bot_username": "oldbot", "enabled": 1,
    }]}
    patched = tmp_path / "legacy.nxbundle"
    with zipfile.ZipFile(bundle) as src, zipfile.ZipFile(patched, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in src.infolist():
            data = src.read(item.filename)
            if item.filename == "manifest.json":
                m = json.loads(data)
                m["contains_channel_credentials"] = True
                m["stripped"] = [x for x in m.get("stripped", []) if x != "im_channel_credentials"]
                data = json.dumps(m).encode()
            dst.writestr(item, data)
        dst.writestr(f"agents/{aid}/channel_credentials.json", json.dumps(legacy))
    pre = await preflight(patched, uid)
    summary = await confirm(pre["preflight_token"], uid)
    assert summary.get("channel_credentials_imported", 0) == 1
    (row,) = await _rows(db_client, "telegram")
    assert row.agent_id != aid and row.enabled is False
    assert row.secret == {"bot_token": "tg-token"} and row.external_id == "4242" and row.public["bot_username"] == "oldbot"


async def test_imported_narramessenger_cursor_is_blank_once_activated(db_client, tmp_workspace_root, tmp_path):
    """The exported public view carries the SOURCE environment's Matrix /sync
    cursor and import lands the row disabled. Activating it must start a
    cursor-less first sync (whose historic filter drops the backlog), not resume
    a cursor that belongs to another homeserver session."""
    from narranexus.platform.bundle.builder import ExportSelection, build_bundle
    from narranexus.platform.bundle.importer import preflight, confirm
    from narranexus.platform.channel.credential_store import GenericCredentialStore

    aid, uid = "agent_cred0090", "test_user"
    await _seed_agent(db_client, aid, "Creddy90", uid)
    store = GenericCredentialStore(db_client)
    await store.upsert("narramessenger", aid, {
        "matrix_homeserver_url": "https://h", "matrix_user_id": "@agent-x:h",
        "matrix_access_token": "at", "matrix_since_token": "source_cursor",
    })
    bundle = tmp_path / "b.nxbundle"
    await build_bundle(uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle)
    await store.unbind("narramessenger", aid)  # the same bot may not be bound twice in one env
    pre = await preflight(bundle, uid)
    await confirm(pre["preflight_token"], uid)

    (imported,) = await store.list_all("narramessenger")
    assert imported.enabled is False
    print("PUB", imported.public, "SEC", sorted(imported.secret))
    assert imported.secret.get("matrix_since_token") == "source_cursor"  # premise
    assert await store.set_enabled("narramessenger", imported.agent_id, True)
    assert (await store.get("narramessenger", imported.agent_id)).secret["matrix_since_token"] == ""
