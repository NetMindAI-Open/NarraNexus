"""
@file_name: test_matrix_bind_service.py
@date: 2026-07-02
@description: NarraMessenger Direct Matrix bind flow (Commit 6).

Locks the contract discovered from the 2026-07-02 setup-guide probe:

- The setup guide's ``Matrix Connection Details`` table row reveals the
  Matrix access token (``syt_...``); ``_parse_setup_guide`` MUST extract it
  in addition to bearer / homeserver / user_id.
- ``do_bind`` calls ``POST /bind-agent/runtime-ready?token=<bind_token>``
  (NOT the old ``POST /api/agent-gateway/connect``) and stores the response
  ``roomId`` as ``bind_room_id``.
- The stored credential row has ``connection_mode='matrix'`` and populates
  ``matrix_access_token`` from the guide, so MatrixTrigger's credential
  watcher picks it up.
- If the guide reveals bearer but not matrix_access_token,
  ``do_bind`` returns a clean error rather than persisting a half-provisioned
  Matrix-mode row (which MatrixTrigger.connect would reject with
  ValueError anyway, only more noisily).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.module.narramessenger_module import (
    _narramessenger_service as svc,
)
from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredentialManager,
)


# ────────────────────────────────────────────────────────────────────
# _parse_setup_guide — regex extraction
# ────────────────────────────────────────────────────────────────────


CONNECTED_GUIDE_FRAGMENT = """
# Narra Messenger Agent Integration

> **Bind Flow Status**: `waiting_connection`

## Your Agent Identity
- **Agent ID (Principal ID)**: 00000000-0000-0000-0000-000000000000
- **Matrix User ID**: @agent-testonly:matrix.netmind.chat

## Authentication
All requests use the header:
```
Authorization: Bearer deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
```

## Matrix Connection Details
| Field | Value |
|-------|-------|
| Homeserver URL | `https://matrix.netmind.chat` |
| User ID | `@agent-testonly:matrix.netmind.chat` |
| Access Token | `syt_FAKETESTtoken_do_not_use_00000000` |
"""


def test_parser_extracts_all_four_credential_fields():
    out = svc._parse_setup_guide(CONNECTED_GUIDE_FRAGMENT)
    assert out["bearer"] == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert out["matrix_access_token"] == "syt_FAKETESTtoken_do_not_use_00000000"
    assert out["homeserver"] == "https://matrix.netmind.chat"
    assert out["matrix_user_id"] == "@agent-testonly:matrix.netmind.chat"


def test_parser_returns_empty_when_bearer_not_yet_revealed():
    """Pre-``waiting_connection`` guide has none of these fields — the
    parser must return an empty-ish dict (no crashes, no fabricated
    values)."""
    early = """
    # Narra Messenger Agent Integration
    > **Bind Flow Status**: `awaiting_profile`
    Post your agent profile to advance.
    """
    out = svc._parse_setup_guide(early)
    assert "bearer" not in out
    assert "matrix_access_token" not in out


def test_parser_skips_syt_lookalikes_that_are_too_short():
    """The regex constrains to syt_ + 20+ alphanumerics. A stray
    ``syt_abc`` example in prose must not be picked up."""
    fragment = "Access Token: `syt_short`"
    out = svc._parse_setup_guide(fragment)
    assert "matrix_access_token" not in out


# ────────────────────────────────────────────────────────────────────
# do_bind — orchestration
# ────────────────────────────────────────────────────────────────────


class _FakeCredentialManager:
    """In-memory upsert recorder."""

    def __init__(self):
        self.upserts = []

    async def upsert(self, cred):
        self.upserts.append(cred)


@pytest.fixture
def fake_manager(monkeypatch):
    fake = _FakeCredentialManager()

    def _factory(_db):
        return fake

    monkeypatch.setattr(
        svc, "NarramessengerCredentialManager", _factory
    )
    return fake


@pytest.fixture
def stub_db():
    """A minimal DB stub that returns None from get_one so
    _agent_profile falls through to defaults ("NarraNexus Agent")."""
    async def _get_one(_table, _filters):
        return None
    return SimpleNamespace(get_one=_get_one)


@pytest.fixture
def guide_scripts(monkeypatch):
    """Sequence of responses for _get_text / _post_json.

    Each test writes into these lists in order — the first _get_text
    call reads guide_scripts["guides"][0], etc. Failing to script
    enough responses raises IndexError, which points at the test.
    """
    state = {
        "guides": [],       # sequential responses to _get_text
        "post_calls": [],   # records (url, body, bearer) tuples
        "post_responses": [],  # sequential responses for _post_json
    }

    async def _fake_get(_session, url):
        return state["guides"].pop(0) if state["guides"] else ""

    async def _fake_post(_session, url, body, bearer=""):
        state["post_calls"].append((url, body, bearer))
        return state["post_responses"].pop(0) if state["post_responses"] else {"ok": False, "error": "no scripted response"}

    monkeypatch.setattr(svc, "_get_text", _fake_get)
    monkeypatch.setattr(svc, "_post_json", _fake_post)
    return state


@pytest.mark.asyncio
async def test_do_bind_calls_runtime_ready_and_stores_matrix_creds(
    stub_db, fake_manager, guide_scripts
):
    """Happy path: guide already reveals bearer + access_token; do_bind
    calls /bind-agent/runtime-ready and stores connection_mode='matrix'."""
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        # runtime-ready succeeds
        {"ok": True, "data": {
            "status": "connected",
            "matrixUserId": "@agent-testonly:matrix.netmind.chat",
            "principalId": "00000000-0000-0000-0000-000000000000",
            "roomId": "!bindroom:matrix.netmind.chat",
        }},
    ]

    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )

    assert result["success"] is True
    # Runtime-ready endpoint URL exact match — locks the "not
    # /api/agent-gateway/connect" contract.
    assert len(guide_scripts["post_calls"]) == 1
    url, _body, _bearer = guide_scripts["post_calls"][0]
    assert url == "https://api.netmind.chat/bind-agent/runtime-ready?token=yGO3BL"

    # Credential upserted with Matrix-mode fields populated.
    assert len(fake_manager.upserts) == 1
    cred = fake_manager.upserts[0]
    assert cred.connection_mode == "matrix"
    assert cred.bearer_token == "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    assert cred.matrix_access_token == "syt_FAKETESTtoken_do_not_use_00000000"
    assert cred.matrix_user_id == "@agent-testonly:matrix.netmind.chat"
    assert cred.matrix_homeserver_url == "https://matrix.netmind.chat"
    assert cred.bind_room_id == "!bindroom:matrix.netmind.chat"
    assert cred.enabled is True


@pytest.mark.asyncio
async def test_do_bind_rejects_when_matrix_access_token_missing(
    stub_db, fake_manager, guide_scripts
):
    """If the guide has bearer + homeserver + user_id but no
    Matrix access token, refuse to persist a half-provisioned row —
    MatrixTrigger.connect would raise ValueError on empty access_token
    and the base would disable the credential, which is a worse UX
    than telling the owner to re-bind."""
    fragment_missing_access = """
    ## Authentication
    ```
    Authorization: Bearer deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef
    ```

    ## Matrix Connection Details
    | Field | Value |
    |-------|-------|
    | Homeserver URL | `https://matrix.netmind.chat` |
    | User ID | `@agent-testonly:matrix.netmind.chat` |
    """
    guide_scripts["guides"] = [fragment_missing_access]
    guide_scripts["post_responses"] = [
        {"ok": True, "data": {"status": "connected", "roomId": "!x:h"}},
    ]
    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )
    assert result["success"] is False
    assert "Matrix access token missing" in result["error"]
    # No credential written.
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_do_bind_surfaces_runtime_ready_failure(
    stub_db, fake_manager, guide_scripts
):
    """A 4xx from runtime-ready must return a user-visible error rather
    than persisting anything. Nothing worse than showing a green
    "bound!" toast while the backend has no live binding."""
    guide_scripts["guides"] = [CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        {"ok": False, "status": 409, "error": "TOKEN_ALREADY_USED"},
    ]
    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )
    assert result["success"] is False
    assert "runtime-ready failed" in result["error"]
    assert fake_manager.upserts == []


@pytest.mark.asyncio
async def test_do_bind_first_call_reports_profile_before_runtime_ready(
    stub_db, fake_manager, guide_scripts
):
    """If the initial guide fetch shows no bearer yet, do_bind should
    POST report-profile, refetch the guide, then call runtime-ready.
    This locks the "advance the state machine before demanding creds"
    behaviour so the bind flow doesn't fail on a fresh session."""
    early = "# Narra Messenger Agent Integration\n> **Bind Flow Status**: `awaiting_profile`\n"
    guide_scripts["guides"] = [early, CONNECTED_GUIDE_FRAGMENT]
    guide_scripts["post_responses"] = [
        # report-profile
        {"ok": True, "data": {}},
        # runtime-ready
        {"ok": True, "data": {
            "status": "connected",
            "roomId": "!bindroom:matrix.netmind.chat",
        }},
    ]

    result = await svc.do_bind(
        stub_db,
        agent_id="agent_x",
        bind_command="https://api.netmind.chat/yGO3BL/setup-guide.md",
    )
    assert result["success"] is True
    assert len(guide_scripts["post_calls"]) == 2
    # First call = report-profile, second = runtime-ready.
    assert "/bind-agent/report-profile" in guide_scripts["post_calls"][0][0]
    assert "/bind-agent/runtime-ready" in guide_scripts["post_calls"][1][0]
