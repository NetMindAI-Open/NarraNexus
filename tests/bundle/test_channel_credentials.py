"""
@file_name: test_channel_credentials.py
@author: NetMind.AI
@date: 2026-07-10
@description: Opt-in channel-credential export/import roundtrip tests.

Covers the "带凭据打包" feature (design:
reference/self_notebook/specs/2026-07-10-channel-credential-export-design.md):

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
    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "base_working_path", str(ws))
    monkeypatch.setenv("HOME", str(fake_home))
    return ws


@pytest.fixture
async def db_client(tmp_db_path, monkeypatch):
    from xyz_agent_context.settings import settings as core_settings
    monkeypatch.setattr(core_settings, "database_url", f"sqlite:///{tmp_db_path}")

    from xyz_agent_context.utils import db_factory
    db_factory._clients_by_loop.clear()

    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate

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
                          app_id: str | None = None):
    await db.insert("lark_credentials", {
        "agent_id": agent_id,
        "app_id": app_id or f"cli_{agent_id}",
        "app_secret_ref": "ref_xxx",
        "app_secret_encrypted": "c2VjcmV0",  # base64("secret")
        "brand": "lark",
        "profile_name": profile_name,
        "auth_status": "bot_ready",
        "is_active": is_active,
    })


async def _seed_wechat_cred(db, agent_id: str, owner_user_id: str, enabled: int = 1):
    await db.insert("channel_wechat_credentials", {
        "agent_id": agent_id,
        "bot_token_encoded": "dG9rZW4=",  # base64("token")
        "base_url": "http://localhost:9000",
        "bot_wx_id": "wxid_bot",
        "owner_wx_id": "wxid_owner",
        "owner_user_id": owner_user_id,   # IM-side id — must survive import
        "owner_name": "WX Owner",
        "enabled": enabled,
    })


async def _seed_slack_cred(db, agent_id: str, team_id: str, bot_user_id: str,
                           owner_user_id: str, enabled: int = 1):
    await db.insert("channel_slack_credentials", {
        "agent_id": agent_id,
        "bot_token_encoded": "eG94Yi10b2tlbg==",
        "app_token_encoded": "eGFwcC10b2tlbg==",
        "bot_user_id": bot_user_id,
        "team_id": team_id,
        "team_name": "Test WS",
        "owner_user_id": owner_user_id,   # Slack user id — must survive import
        "enabled": enabled,
    })


def _read_member(bundle_path: Path, member: str):
    with zipfile.ZipFile(bundle_path) as z:
        names = z.namelist()
        if member not in names:
            return None
        return z.read(member).decode("utf-8")


# ---- tests ----

async def test_default_export_excludes_credentials(db_client, tmp_workspace_root, tmp_path):
    """Opt-in flag off (default) → no credential file, manifest flag False."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle

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
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle

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
    assert "lark_credentials" in creds
    assert creds["lark_credentials"][0]["profile_name"] == "prof_optin"
    assert creds["lark_credentials"][0]["agent_id"] == aid

    manifest = json.loads(_read_member(bundle, "manifest.json"))
    assert manifest.get("contains_channel_credentials") is True
    # Preview must NOT claim channel credentials were stripped when they shipped.
    assert "im_channel_credentials" not in manifest.get("stripped", [])
    assert "user_providers" in manifest.get("stripped", [])  # providers never travel


async def test_import_forces_inactive_and_remaps_agent(db_client, tmp_workspace_root, tmp_path):
    """Imported credential lands with enabled=0 (even though source=1) and its
    agent_id is remapped to the freshly-minted agent."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    aid, uid = "agent_cred0003", "test_user"
    await _seed_agent(db_client, aid, "Creddy3", uid)
    await _seed_wechat_cred(db_client, aid, owner_user_id="wx_owner_ABC", enabled=1)

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle
    )
    pre = await preflight(bundle, uid)
    summary = await confirm(pre["preflight_token"], uid)

    rows = await db_client.get("channel_wechat_credentials", {})
    # original (enabled=1, aid) + imported (enabled=0, new aid)
    assert len(rows) == 2
    imported = [r for r in rows if r["agent_id"] != aid]
    assert len(imported) == 1
    assert imported[0]["enabled"] == 0
    # new agent_id must be a real, freshly-minted agent in this DB
    assert imported[0]["agent_id"].startswith("agent_")
    assert await db_client.get_one("agents", {"agent_id": imported[0]["agent_id"]})
    assert summary.get("channel_credentials_imported", 0) == 1


async def test_import_preserves_im_owner_identity(db_client, tmp_workspace_root, tmp_path):
    """owner_user_id is an IM-side id and must NOT be reattributed to the
    recipient NarraNexus user_id by the generic user-attribution rewrite."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

    aid, uid = "agent_cred0004", "test_user"
    await _seed_agent(db_client, aid, "Creddy4", uid)
    await _seed_wechat_cred(db_client, aid, owner_user_id="wx_owner_XYZ", enabled=1)

    bundle = tmp_path / "b.nxbundle"
    await build_bundle(
        uid, ExportSelection(agent_ids=[aid], include_channel_credentials=True), bundle
    )
    pre = await preflight(bundle, uid)
    await confirm(pre["preflight_token"], uid)

    rows = await db_client.get("channel_wechat_credentials", {})
    imported = [r for r in rows if r["agent_id"] != aid][0]
    # The IM owner id survived verbatim — NOT overwritten with "test_user".
    assert imported["owner_user_id"] == "wx_owner_XYZ"


async def test_credential_clash_is_skipped(db_client, tmp_workspace_root, tmp_path):
    """Same-DB roundtrip: the imported Slack credential collides with the
    source's own (team_id, bot_user_id) binding → skipped, not overwritten."""
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

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
    rows = await db_client.get("channel_slack_credentials", {})
    assert len(rows) == 1
    assert rows[0]["agent_id"] == aid
    assert summary.get("channel_credentials_skipped_conflict", 0) == 1


async def test_lark_clash_keys_on_app_id_not_profile_name(db_client, tmp_workspace_root, tmp_path):
    """Lark's bot identity is app_id, NOT the agent-derived profile_name. A bundle
    carrying app_id X must be detected as a clash when X is already bound in the
    target env under a DIFFERENT profile_name — the exact case profile_name missed.
    """
    from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
    from xyz_agent_context.bundle.importer import preflight, confirm

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
    await db_client.delete("lark_credentials", {"agent_id": src})
    other = "agent_larkother1"
    await _seed_agent(db_client, other, "Lark Other", uid)
    await _seed_lark_cred(db_client, other, profile_name="prof_other", app_id=SHARED_APP)

    pre = await preflight(bundle, uid)
    assert pre.get("credential_clashes")  # app_id match → surfaced
    summary = await confirm(pre["preflight_token"], uid)

    # The same Lark app is not double-bound: only the existing (other) row remains.
    rows = await db_client.get("lark_credentials", {})
    assert [r["agent_id"] for r in rows] == [other]
    assert summary.get("channel_credentials_skipped_conflict", 0) == 1
