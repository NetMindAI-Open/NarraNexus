"""
@file_name: test_matrix_send.py
@date: 2026-07-03
@description: Matrix-native outbound helpers — msgtype mapping, workspace
path confinement, and the send_media orchestration (upload → room_send).

The two raw-HTTP helpers (matrix_upload / matrix_room_send) are
monkeypatched at the send_media_impl level so no homeserver is touched;
we exercise the logic that matters: path safety, size cap, msgtype
derivation, and the content payload handed to room_send.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.module.narramessenger_module import _matrix_send as ms
from xyz_agent_context.module.narramessenger_module._matrix_send import (
    MatrixSendError,
    msgtype_for_mime,
    resolve_workspace_file,
    send_media_impl,
)

HOMESERVER = "https://matrix.netmind.chat"
TOKEN = "syt_fake"
ROOM = "!room:matrix.netmind.chat"


# ── msgtype_for_mime ────────────────────────────────────────────────────
@pytest.mark.parametrize("mime,expected", [
    ("image/png", "m.image"),
    ("image/jpeg", "m.image"),
    ("audio/ogg", "m.audio"),
    ("video/mp4", "m.video"),
    ("application/pdf", "m.file"),
    ("", "m.file"),
])
def test_msgtype_for_mime(mime, expected):
    assert msgtype_for_mime(mime) == expected


# ── resolve_workspace_file (the security gate) ──────────────────────────
@pytest.fixture
def workspace(monkeypatch, tmp_path: Path) -> Path:
    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(settings_mod.settings, "base_working_path", str(tmp_path))
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path
    root = agent_workspace_path("agent_x", "user_owner")
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_resolve_relative_path_inside_workspace(workspace):
    (workspace / "out.png").write_bytes(b"x")
    got = resolve_workspace_file("agent_x", "user_owner", "out.png")
    assert got == (workspace / "out.png").resolve()


def test_resolve_rejects_parent_traversal(workspace):
    (workspace.parent / "secret.txt").write_bytes(b"nope")
    with pytest.raises(MatrixSendError) as ei:
        resolve_workspace_file("agent_x", "user_owner", "../secret.txt")
    assert ei.value.code == "bad_path"


def test_resolve_rejects_absolute_outside(workspace):
    with pytest.raises(MatrixSendError) as ei:
        resolve_workspace_file("agent_x", "user_owner", "/etc/passwd")
    assert ei.value.code == "bad_path"


def test_resolve_missing_file(workspace):
    with pytest.raises(MatrixSendError) as ei:
        resolve_workspace_file("agent_x", "user_owner", "ghost.png")
    assert ei.value.code == "not_found"


# ── send_media_impl orchestration ───────────────────────────────────────
@pytest.mark.asyncio
async def test_send_media_happy_path_uploads_then_room_sends(workspace, monkeypatch):
    (workspace / "chart.png").write_bytes(b"\x89PNG fake bytes")

    uploaded = {}
    sent = {}

    async def _fake_upload(*, homeserver, token, filename, mime_type, data):
        uploaded.update(filename=filename, mime=mime_type, n=len(data))
        return "mxc://matrix.netmind.chat/AbC123"

    async def _fake_room_send(*, homeserver, token, room_id, content, txn_id=None):
        sent.update(room_id=room_id, content=content)
        return "$evt_1"

    monkeypatch.setattr(ms, "matrix_upload", _fake_upload)
    monkeypatch.setattr(ms, "matrix_room_send", _fake_room_send)

    res = await send_media_impl(
        agent_id="agent_x", owner_id="user_owner",
        homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
        file_path="chart.png", max_bytes=50 * 1024 * 1024,
    )
    assert res["ok"] is True
    assert res["event_id"] == "$evt_1"
    assert res["msgtype"] == "m.image"
    # content handed to room_send is a well-formed m.image event.
    c = sent["content"]
    assert c["msgtype"] == "m.image"
    assert c["url"] == "mxc://matrix.netmind.chat/AbC123"
    assert c["body"] == "chart.png"          # no caption → filename
    assert c["filename"] == "chart.png"
    assert c["info"]["mimetype"] == "image/png"
    assert c["info"]["size"] == len(b"\x89PNG fake bytes")


@pytest.mark.asyncio
async def test_send_media_caption_becomes_body_keeps_filename(workspace, monkeypatch):
    (workspace / "report.pdf").write_bytes(b"%PDF-1.4 fake")

    sent = {}

    async def _fake_upload(**_kw):
        return "mxc://h/x"

    async def _fake_room_send(*, homeserver, token, room_id, content, txn_id=None):
        sent.update(content=content)
        return "$e"

    monkeypatch.setattr(ms, "matrix_upload", _fake_upload)
    monkeypatch.setattr(ms, "matrix_room_send", _fake_room_send)

    res = await send_media_impl(
        agent_id="agent_x", owner_id="user_owner",
        homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
        file_path="report.pdf", max_bytes=50 * 1024 * 1024,
        caption="Here is the Q3 report",
    )
    assert res["ok"] is True
    assert res["msgtype"] == "m.file"
    assert sent["content"]["body"] == "Here is the Q3 report"
    assert sent["content"]["filename"] == "report.pdf"


@pytest.mark.asyncio
async def test_send_media_oversized_skips_upload(workspace, monkeypatch):
    (workspace / "big.bin").write_bytes(b"0123456789")

    called = {"upload": False}

    async def _fake_upload(**_kw):
        called["upload"] = True
        return "mxc://h/x"

    monkeypatch.setattr(ms, "matrix_upload", _fake_upload)

    res = await send_media_impl(
        agent_id="agent_x", owner_id="user_owner",
        homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
        file_path="big.bin", max_bytes=5,  # 10 bytes > 5
    )
    assert res["ok"] is False
    assert res["error"] == "oversized"
    assert called["upload"] is False


@pytest.mark.asyncio
async def test_send_media_bad_path_returns_error(workspace, monkeypatch):
    res = await send_media_impl(
        agent_id="agent_x", owner_id="user_owner",
        homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
        file_path="../../etc/passwd", max_bytes=50 * 1024 * 1024,
    )
    assert res["ok"] is False
    assert res["error"] == "bad_path"


@pytest.mark.asyncio
async def test_send_media_upload_failure_never_raises(workspace, monkeypatch):
    (workspace / "a.png").write_bytes(b"x")

    async def _boom(**_kw):
        raise MatrixSendError("http_error", "status 413")

    monkeypatch.setattr(ms, "matrix_upload", _boom)

    res = await send_media_impl(
        agent_id="agent_x", owner_id="user_owner",
        homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
        file_path="a.png", max_bytes=50 * 1024 * 1024,
    )
    assert res["ok"] is False
    assert res["error"] == "http_error"


# ── raw HTTP helpers (matrix_room_send / matrix_upload) ─────────────────
class _FakeResp:
    def __init__(self, status: int, json_data: dict):
        self.status = status
        self._json = json_data

    async def json(self):
        return self._json

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeSession:
    def __init__(self, resp: _FakeResp, record: dict):
        self._resp = resp
        self._record = record

    def put(self, url, headers=None, json=None):
        self._record.update(method="PUT", url=url, headers=headers, json=json)
        return self._resp

    def post(self, url, headers=None, params=None, data=None):
        self._record.update(
            method="POST", url=url, headers=headers, params=params, data=data
        )
        return self._resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_session(monkeypatch, resp: _FakeResp, record: dict):
    monkeypatch.setattr(
        ms.aiohttp, "ClientSession",
        lambda timeout=None: _FakeSession(resp, record),
    )


@pytest.mark.asyncio
async def test_matrix_room_send_puts_and_returns_event_id(monkeypatch):
    rec: dict = {}
    _patch_session(monkeypatch, _FakeResp(200, {"event_id": "$abc"}), rec)

    ev = await ms.matrix_room_send(
        homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
        content={"msgtype": "m.text", "body": "hi"}, txn_id="t1",
    )
    assert ev == "$abc"
    assert rec["method"] == "PUT"
    assert rec["url"].endswith(f"/rooms/{ROOM}/send/m.room.message/t1")
    assert rec["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert rec["json"]["body"] == "hi"


@pytest.mark.asyncio
async def test_matrix_room_send_error_status_raises(monkeypatch):
    rec: dict = {}
    _patch_session(
        monkeypatch,
        _FakeResp(403, {"errcode": "M_FORBIDDEN", "error": "nope"}), rec,
    )
    with pytest.raises(MatrixSendError) as ei:
        await ms.matrix_room_send(
            homeserver=HOMESERVER, token=TOKEN, room_id=ROOM,
            content={"msgtype": "m.text", "body": "x"},
        )
    assert ei.value.code == "M_FORBIDDEN"


@pytest.mark.asyncio
async def test_matrix_upload_returns_content_uri(monkeypatch):
    rec: dict = {}
    _patch_session(
        monkeypatch, _FakeResp(200, {"content_uri": "mxc://h/xyz"}), rec,
    )
    mxc = await ms.matrix_upload(
        homeserver=HOMESERVER, token=TOKEN,
        filename="a.png", mime_type="image/png", data=b"bytes",
    )
    assert mxc == "mxc://h/xyz"
    assert rec["method"] == "POST"
    assert rec["url"].endswith("/_matrix/media/v3/upload")
    assert rec["params"] == {"filename": "a.png"}
    assert rec["headers"]["Content-Type"] == "image/png"


@pytest.mark.asyncio
async def test_matrix_upload_missing_content_uri_raises(monkeypatch):
    rec: dict = {}
    _patch_session(monkeypatch, _FakeResp(200, {}), rec)
    with pytest.raises(MatrixSendError):
        await ms.matrix_upload(
            homeserver=HOMESERVER, token=TOKEN,
            filename="a.png", mime_type="image/png", data=b"x",
        )
