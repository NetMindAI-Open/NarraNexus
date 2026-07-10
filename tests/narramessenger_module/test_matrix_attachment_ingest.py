"""
@file_name: test_matrix_attachment_ingest.py
@date: 2026-07-03
@description: Phase 3 — multimodal ingest for MatrixTrigger.

Locks the receive path for ``m.image`` / ``m.file`` / ``m.audio`` /
``m.video`` events:
  - ``_wrap_event`` marshals a matrix-nio media event into a raw dict
    (mxc url + mimetype + size + filename) instead of dropping it
  - ``parse_event`` turns the media raw dict into a ParsedMessage with
    ``raw["attachment_refs"]`` populated + the right content_type, and
    lets caption-less media through (empty body)
  - ``fetch_attachments`` downloads each mxc via the authenticated
    endpoint (stubbed here) → ``_persist_attachment`` → audit, and
    audits oversized / fetch-failure correctly without raising

The mxc download is monkeypatched per test (``_download_mxc``) so no
real network is touched — we exercise the trigger's contract, not the
homeserver.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
)
from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixMediaError,
    MatrixTrigger,
)
from xyz_agent_context.schema.attachment_schema import AttachmentCategory
from xyz_agent_context.schema.parsed_message import MessageContentType


HOMESERVER = "matrix.netmind.chat"
AGENT_MXID = f"@agent-abc:{HOMESERVER}"
STRANGER_ID = f"@bob:{HOMESERVER}"

_FAKE_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
    b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _cred(**overrides) -> NarramessengerCredential:
    base = dict(
        agent_id="agent_x",
        bearer_token="tok",
        matrix_homeserver_url=f"https://{HOMESERVER}",
        matrix_user_id=AGENT_MXID,
        matrix_access_token="syt_fake_token",
    )
    base.update(overrides)
    return NarramessengerCredential(**base)


def _nio_media_event(
    *,
    mxc: str = f"mxc://{HOMESERVER}/AbCdEf123456",
    body: str = "photo.png",
    msgtype: str = "m.image",
    mimetype: str = "image/png",
    size: int = len(_FAKE_PNG),
    event_id: str = "$evt_media",
    sender: str = STRANGER_ID,
) -> Any:
    """A real matrix-nio ``RoomMessageImage`` carrying only the attributes
    ``_wrap_event`` reads.

    Built via ``__new__`` (bypassing nio's parser __init__) so the
    ``isinstance(event, RoomMessageMedia)`` gate in ``_wrap_event`` sees a
    genuine media subclass — a duck-typed stand-in would be dropped, which
    is exactly the behaviour the gate is meant to enforce.
    """
    from nio import RoomMessageImage

    info: dict[str, Any] = {}
    if mimetype:
        info["mimetype"] = mimetype
    if size:
        info["size"] = size
    ev = RoomMessageImage.__new__(RoomMessageImage)
    ev.url = mxc
    ev.body = body
    ev.event_id = event_id
    ev.sender = sender
    ev.server_timestamp = 1_700_000_000_000
    ev.source = {"content": {"msgtype": msgtype, "url": mxc, "info": info}}
    return ev


def _media_raw(**overrides) -> dict:
    """The dict shape ``_wrap_event`` yields for a media event."""
    base = dict(
        kind="m.room.message.media",
        event_id="$evt_media",
        room_id="!room:h",
        sender_id=STRANGER_ID,
        server_ts=1_700_000_000_000,
        body="photo.png",
        mxc_url=f"mxc://{HOMESERVER}/AbCdEf123456",
        mimetype="image/png",
        size=len(_FAKE_PNG),
        msgtype="m.image",
        _agent_id="agent_x",
        _our_user_id=AGENT_MXID,
    )
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────
# _wrap_event — media event → raw dict (no longer dropped)
# ────────────────────────────────────────────────────────────────────


def test_wrap_event_marshals_image_instead_of_dropping():
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_nio_media_event(),
        room_id="!room:h",
        credential=_cred(),
    )
    assert raw is not None
    assert raw["kind"] == "m.room.message.media"
    assert raw["mxc_url"] == f"mxc://{HOMESERVER}/AbCdEf123456"
    assert raw["mimetype"] == "image/png"
    assert raw["size"] == len(_FAKE_PNG)
    assert raw["body"] == "photo.png"
    assert raw["msgtype"] == "m.image"


def test_wrap_event_text_still_works():
    """Regression: text events keep their existing shape."""
    from nio import RoomMessageText

    ev = RoomMessageText.__new__(RoomMessageText)
    ev.event_id = "$t"
    ev.sender = STRANGER_ID
    ev.server_timestamp = 1
    ev.body = "hello"
    raw = t = MatrixTrigger()._wrap_event(
        event=ev, room_id="!room:h", credential=_cred()
    )
    assert raw["kind"] == "m.room.message.text"


# ────────────────────────────────────────────────────────────────────
# parse_event — media raw → attachment_refs + content_type
# ────────────────────────────────────────────────────────────────────


def test_parse_event_image_populates_refs_and_content_type():
    t = MatrixTrigger()
    parsed = t.parse_event(_media_raw())
    assert parsed is not None
    assert parsed.content_type == MessageContentType.IMAGE
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    ref = refs[0]
    assert ref["mxc_url"] == f"mxc://{HOMESERVER}/AbCdEf123456"
    assert ref["original_name"] == "photo.png"
    assert ref["mime_hint"] == "image/png"
    assert ref["size_hint"] == len(_FAKE_PNG)


def test_parse_event_caption_less_image_is_not_dropped():
    """Pure image with empty body must still flow (refs carry it)."""
    t = MatrixTrigger()
    parsed = t.parse_event(_media_raw(body=""))
    assert parsed is not None
    assert (parsed.raw.get("attachment_refs") or [])


def test_parse_event_audio_and_video_content_types():
    t = MatrixTrigger()
    audio = t.parse_event(
        _media_raw(msgtype="m.audio", mimetype="audio/ogg", body="v.ogg")
    )
    video = t.parse_event(
        _media_raw(msgtype="m.video", mimetype="video/mp4", body="c.mp4")
    )
    assert audio.content_type == MessageContentType.AUDIO
    assert video.content_type == MessageContentType.VIDEO


def test_parse_event_file_content_type_for_pdf():
    t = MatrixTrigger()
    parsed = t.parse_event(
        _media_raw(msgtype="m.file", mimetype="application/pdf", body="r.pdf")
    )
    assert parsed.content_type == MessageContentType.FILE


def test_parse_event_media_without_mxc_dropped():
    t = MatrixTrigger()
    assert t.parse_event(_media_raw(mxc_url="")) is None


# ────────────────────────────────────────────────────────────────────
# fetch_attachments — download + persist + audit
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(monkeypatch, tmp_path: Path) -> Path:
    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "base_working_path", str(tmp_path)
    )
    return tmp_path


@pytest.fixture
def trigger_with_owner(db_client, isolated_workspace):
    async def _setup():
        await db_client.insert("agents", {
            "agent_id": "agent_x",
            "agent_name": "FakeAgent",
            "created_by": "user_owner",
            "is_public": 0,
        })
        trigger = MatrixTrigger()
        trigger._db = db_client
        from xyz_agent_context.repository.channel_trigger_audit_repository import (
            ChannelTriggerAuditRepository,
        )
        trigger._audit_repo = ChannelTriggerAuditRepository(
            "narramessenger", db_client
        )
        return trigger

    return _setup


@pytest.mark.asyncio
async def test_fetch_attachments_downloads_and_persists_image(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    trigger = await trigger_with_owner()
    cred = _cred()

    calls: list[tuple[str, str]] = []

    async def _stub_download(*, credential, server_name, media_id, max_bytes):
        calls.append((server_name, media_id))
        return _FAKE_PNG

    monkeypatch.setattr(trigger, "_download_mxc", _stub_download)

    parsed = trigger.parse_event(_media_raw())
    attachments = await trigger.fetch_attachments(parsed, cred)

    assert len(attachments) == 1
    att = attachments[0]
    assert att.original_name == "photo.png"
    assert att.mime_type == "image/png"
    assert att.category == AttachmentCategory.IMAGE
    assert calls == [(HOMESERVER, "AbCdEf123456")]

    # The bytes are readable at exactly the path synthesize_marker will
    # announce to the agent (resolve_attachment_path == the marker's
    # path=). This is the prompt-download-location contract: the agent's
    # Read tool must find the file where the marker says it is.
    from xyz_agent_context.utils.attachment_storage import (
        resolve_attachment_path,
    )
    on_disk = resolve_attachment_path("agent_x", "user_owner", att.file_id)
    assert on_disk is not None
    assert Path(on_disk).read_bytes() == _FAKE_PNG

    # And the marker string itself carries that path + the Read hint.
    marker = att.synthesize_marker("agent_x", "user_owner")
    assert str(on_disk) in marker
    assert "use Read tool to view" in marker

    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "narramessenger", "event_type": EVENT_ATTACHMENT_PERSISTED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_audits_oversized_before_download(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    trigger = await trigger_with_owner()
    cred = _cred()
    dl_calls: list[Any] = []

    async def _stub_download(*, credential, server_name, media_id, max_bytes):
        dl_calls.append(media_id)
        return b"x"

    monkeypatch.setattr(trigger, "_download_mxc", _stub_download)

    from backend.config import settings as backend_settings
    monkeypatch.setattr(backend_settings, "max_upload_bytes", 1024)

    parsed = trigger.parse_event(_media_raw(size=10_000))  # > 1024
    attachments = await trigger.fetch_attachments(parsed, cred)

    assert attachments == []
    assert dl_calls == []  # never downloaded

    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "narramessenger", "event_type": EVENT_INGRESS_DROPPED_OVERSIZED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_oversized_during_stream(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """Server lies about size (size_hint 0) but the stream exceeds the
    cap → MatrixMediaError('oversized') → OVERSIZED audit, not FAILED."""
    trigger = await trigger_with_owner()
    cred = _cred()

    async def _stub_download(*, credential, server_name, media_id, max_bytes):
        raise MatrixMediaError("oversized", "stream exceeded cap")

    monkeypatch.setattr(trigger, "_download_mxc", _stub_download)

    parsed = trigger.parse_event(_media_raw(size=0))
    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []

    oversized = await db_client.get(
        "channel_trigger_audit",
        {"channel": "narramessenger", "event_type": EVENT_INGRESS_DROPPED_OVERSIZED},
    )
    assert len(oversized) == 1
    failures = await db_client.get(
        "channel_trigger_audit",
        {"channel": "narramessenger", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(failures) == 0


@pytest.mark.asyncio
async def test_fetch_attachments_audits_fetch_failure(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    trigger = await trigger_with_owner()
    cred = _cred()

    async def _stub_download(*, credential, server_name, media_id, max_bytes):
        raise MatrixMediaError("http_error", "status 502")

    monkeypatch.setattr(trigger, "_download_mxc", _stub_download)

    parsed = trigger.parse_event(_media_raw(size=0))
    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []

    failures = await db_client.get(
        "channel_trigger_audit",
        {"channel": "narramessenger", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(failures) == 1
