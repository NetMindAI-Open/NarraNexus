"""
@file_name: test_telegram_attachment_ingest.py
@date: 2026-05-20
@description: Phase 1a — end-to-end attachment ingest tests for
``TelegramTrigger.parse_event`` (refs extraction) and
``fetch_attachments`` (SDK download → ``_persist_attachment`` → audit).

The SDK download method is monkey-patched per test so no real network
is touched; we focus on the trigger's contract:
  - parse_event populates ``raw["attachment_refs"]`` for document / photo
    / voice / audio / video
  - photo[] picks the LAST (largest) entry
  - caption is preserved into ``ParsedMessage.content``
  - mixed entities + caption_entities → mentions merged
  - sticker still returns None (Phase 1a NOT Building)
  - fetch_attachments downloads each ref, audits success / failure /
    oversized correctly
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
)
from xyz_agent_context.module.telegram_module._telegram_credential_manager import (
    TelegramCredential,
)
from xyz_agent_context.module.telegram_module.telegram_sdk_client import (
    TELEGRAM_BOT_DOWNLOAD_CAP_BYTES,
    TelegramSDKError,
)
from xyz_agent_context.module.telegram_module.telegram_trigger import (
    TelegramTrigger,
)
from xyz_agent_context.schema.attachment_schema import AttachmentCategory
from xyz_agent_context.schema.parsed_message import MessageContentType


_FAKE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog >> endobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer << /Size 1 >>\nstartxref\n0\n%%EOF\n"
)


def _cred() -> TelegramCredential:
    return TelegramCredential(
        agent_id="agent_a",
        bot_token="1234:tok",
        bot_user_id="1001",
        bot_username="acme_bot",
    )


def _msg(**overrides) -> dict:
    base = {
        "update_id": 100,
        "message": {
            "message_id": 7,
            "date": 1700000000,
            "from": {"id": 42, "first_name": "Ada", "last_name": "Lovelace"},
            "chat": {"id": 99, "type": "private"},
        },
    }
    base["message"].update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────
# parse_event — refs extraction per media kind
# ────────────────────────────────────────────────────────────────────


def test_parse_event_document_creates_ref_and_file_content_type() -> None:
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="",
            caption="see this report",
            document={
                "file_id": "BAADdoc",
                "file_unique_id": "uniq_doc",
                "file_name": "report.pdf",
                "mime_type": "application/pdf",
                "file_size": 154823,
            },
        )
    )
    assert parsed is not None
    assert parsed.content == "see this report"  # caption preserved
    assert parsed.content_type == MessageContentType.FILE
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    ref = refs[0]
    assert ref["platform_ref"] == "BAADdoc"
    assert ref["original_name"] == "report.pdf"
    assert ref["mime_hint"] == "application/pdf"
    assert ref["size_hint"] == 154823


def test_parse_event_photo_picks_largest_entry() -> None:
    """photo[] is ordered smallest → largest; the trigger picks [-1]."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="",
            photo=[
                {"file_id": "small", "file_unique_id": "u_s", "file_size": 100},
                {"file_id": "medium", "file_unique_id": "u_m", "file_size": 1000},
                {"file_id": "large", "file_unique_id": "u_l", "file_size": 10000},
            ],
        )
    )
    assert parsed is not None
    assert parsed.content_type == MessageContentType.IMAGE
    refs = parsed.raw["attachment_refs"]
    assert len(refs) == 1
    assert refs[0]["platform_ref"] == "large"
    assert refs[0]["original_name"].startswith("u_l")  # uses file_unique_id


def test_parse_event_voice_message_creates_audio_ref() -> None:
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="",
            voice={
                "file_id": "vox123",
                "file_unique_id": "uvox",
                "mime_type": "audio/ogg",
                "duration": 12,
                "file_size": 38000,
            },
        )
    )
    assert parsed is not None
    assert parsed.content_type == MessageContentType.AUDIO
    refs = parsed.raw["attachment_refs"]
    assert refs[0]["kind"] == "voice"
    assert refs[0]["mime_hint"] == "audio/ogg"
    assert refs[0]["original_name"].endswith(".ogg")


def test_parse_event_audio_message_creates_audio_ref() -> None:
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="",
            audio={
                "file_id": "audio_abc",
                "file_unique_id": "uaudio",
                "mime_type": "audio/mpeg",
                "file_name": "song.mp3",
                "file_size": 5_000_000,
            },
        )
    )
    assert parsed is not None
    refs = parsed.raw["attachment_refs"]
    assert refs[0]["kind"] == "audio"
    assert refs[0]["mime_hint"] == "audio/mpeg"
    # Telegram audio carries file_name when uploaded as MP3 with metadata.
    assert refs[0]["original_name"] == "song.mp3"


def test_parse_event_video_message_creates_video_ref() -> None:
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="",
            video={
                "file_id": "vid_xyz",
                "file_unique_id": "uvid",
                "mime_type": "video/mp4",
                "file_size": 12_345_678,
            },
        )
    )
    assert parsed is not None
    assert parsed.content_type == MessageContentType.VIDEO
    refs = parsed.raw["attachment_refs"]
    assert refs[0]["kind"] == "video"


def test_parse_event_text_only_message_unchanged() -> None:
    """Regression: pure text messages still pass through without refs."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(_msg(text="hi"))
    assert parsed is not None
    assert parsed.content == "hi"
    assert parsed.content_type == MessageContentType.TEXT
    # No refs key — message.raw is the original dict, no attachment_refs added.
    assert "attachment_refs" not in (parsed.raw or {})


def test_parse_event_sticker_still_returns_none() -> None:
    """Stickers are NOT in Phase 1a's recognized media kinds.
    No text + no recognized media → drop (matches the original
    text-only contract for unsupported types)."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(text="", sticker={"emoji": ":-)"})
    )
    assert parsed is None


def test_parse_event_caption_entities_merge_with_entities() -> None:
    """When a media message carries a caption, mentions live under
    ``caption_entities``, not ``entities``. Both sources are merged."""
    trigger = TelegramTrigger()
    parsed = trigger.parse_event(
        _msg(
            text="",
            caption="hi @teammate look",
            caption_entities=[
                {"type": "mention", "offset": 3, "length": 9},
            ],
            document={
                "file_id": "doc1",
                "file_unique_id": "u_doc1",
                "mime_type": "application/pdf",
                "file_size": 100,
            },
        )
    )
    assert parsed is not None
    assert "teammate" in parsed.mentions


# ────────────────────────────────────────────────────────────────────
# fetch_attachments — download + persist + audit
# ────────────────────────────────────────────────────────────────────


@pytest.fixture
def isolated_workspace(monkeypatch, tmp_path: Path) -> Path:
    """Redirect BASE_WORKING_PATH so tests don't write to the real workspace."""
    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "base_working_path", str(tmp_path)
    )
    return tmp_path


@pytest.fixture
def trigger_with_owner(db_client, isolated_workspace):
    """A TelegramTrigger wired to db_client + an agent row with a known owner."""
    async def _setup():
        await db_client.insert("agents", {
            "agent_id": "agent_a",
            "agent_name": "FakeAgent",
            "created_by": "user_owner",
            "is_public": 0,
        })
        trigger = TelegramTrigger()
        # Inject deps the base would normally set inside start()
        trigger._db = db_client
        from xyz_agent_context.repository.channel_trigger_audit_repository import (
            ChannelTriggerAuditRepository,
        )
        trigger._audit_repo = ChannelTriggerAuditRepository("telegram", db_client)
        return trigger

    return _setup


@pytest.mark.asyncio
async def test_fetch_attachments_downloads_and_persists_pdf(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """Full ingest path: ref → SDK.download_file → _persist_attachment →
    Attachment + audit row."""
    trigger = await trigger_with_owner()
    cred = _cred()

    # Stub the SDK client's download_file.
    download_calls: list[tuple[str, int | None]] = []

    class _StubClient:
        async def download_file(self, file_id, *, size_hint=None):
            download_calls.append((file_id, size_hint))
            return _FAKE_PDF, "documents/report.pdf"

        async def close(self):
            pass

    # When fetch_attachments doesn't find a cached client, it spins one up.
    # We patch the constructor so the call returns our stub.
    import xyz_agent_context.module.telegram_module.telegram_trigger as tg_mod
    monkeypatch.setattr(
        tg_mod, "TelegramSDKClient", lambda *_a, **_kw: _StubClient()
    )

    # Build a parsed message via parse_event so the test exercises the
    # contract end-to-end.
    parsed = trigger.parse_event(_msg(
        text="",
        caption="report attached",
        document={
            "file_id": "BAADdoc",
            "file_unique_id": "uniq",
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
            "file_size": len(_FAKE_PDF),
        },
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert len(attachments) == 1
    att = attachments[0]
    assert att.original_name == "report.pdf"
    assert att.mime_type == "application/pdf"
    assert att.category == AttachmentCategory.DOCUMENT
    assert att.transcript is None  # PDF, not audio
    # download_file received the right file_id + size_hint.
    assert download_calls == [("BAADdoc", len(_FAKE_PDF))]

    # Bytes landed on disk under the OWNER's workspace path.
    pdf_path = isolated_workspace / "agent_a_user_owner" / "user_upload_files"
    assert any(p.read_bytes() == _FAKE_PDF for p in pdf_path.rglob("att_*.pdf"))

    # EVENT_ATTACHMENT_PERSISTED audited.
    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "telegram", "event_type": EVENT_ATTACHMENT_PERSISTED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_audits_oversized_before_download(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """size_hint > backend.max_upload_bytes → audit + skip BEFORE
    download_file is even called."""
    trigger = await trigger_with_owner()
    cred = _cred()
    sdk_calls: list[Any] = []

    class _Stub:
        async def download_file(self, file_id, *, size_hint=None):
            sdk_calls.append(file_id)
            return b"x", "x"

        async def close(self):
            pass

    import xyz_agent_context.module.telegram_module.telegram_trigger as tg_mod
    monkeypatch.setattr(tg_mod, "TelegramSDKClient", lambda *_a, **_kw: _Stub())

    # Force backend cap to a tiny value so size_hint trivially exceeds it.
    from backend.config import settings as backend_settings
    monkeypatch.setattr(backend_settings, "max_upload_bytes", 1024)

    parsed = trigger.parse_event(_msg(
        text="",
        document={
            "file_id": "huge",
            "file_unique_id": "u_huge",
            "file_name": "huge.zip",
            "mime_type": "application/zip",
            "file_size": 10_000,  # > 1024
        },
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []
    assert sdk_calls == []  # download_file NOT called

    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "telegram", "event_type": EVENT_INGRESS_DROPPED_OVERSIZED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_audits_telegram_20mb_cap(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """SDK raises TelegramSDKError(code='oversized') → audit OVERSIZED,
    not FETCH_FAILED. Lets ops distinguish platform-cap from network."""
    trigger = await trigger_with_owner()
    cred = _cred()

    class _Stub:
        async def download_file(self, file_id, *, size_hint=None):
            raise TelegramSDKError("oversized", "file exceeds 20 MB cap")

        async def close(self):
            pass

    import xyz_agent_context.module.telegram_module.telegram_trigger as tg_mod
    monkeypatch.setattr(tg_mod, "TelegramSDKClient", lambda *_a, **_kw: _Stub())

    parsed = trigger.parse_event(_msg(
        text="",
        document={
            "file_id": "x",
            "file_unique_id": "u",
            "file_name": "big.pdf",
            "mime_type": "application/pdf",
            "file_size": TELEGRAM_BOT_DOWNLOAD_CAP_BYTES + 1,
        },
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []

    oversized = await db_client.get(
        "channel_trigger_audit",
        {"channel": "telegram", "event_type": EVENT_INGRESS_DROPPED_OVERSIZED},
    )
    assert len(oversized) == 1

    failures = await db_client.get(
        "channel_trigger_audit",
        {"channel": "telegram", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(failures) == 0


@pytest.mark.asyncio
async def test_fetch_attachments_audits_fetch_failure(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """Network error during download → EVENT_ATTACHMENT_FETCH_FAILED,
    empty result, never-raise."""
    trigger = await trigger_with_owner()
    cred = _cred()

    class _Stub:
        async def download_file(self, file_id, *, size_hint=None):
            raise TelegramSDKError("client_error:ClientConnectionError", "boom")

        async def close(self):
            pass

    import xyz_agent_context.module.telegram_module.telegram_trigger as tg_mod
    monkeypatch.setattr(tg_mod, "TelegramSDKClient", lambda *_a, **_kw: _Stub())

    parsed = trigger.parse_event(_msg(
        text="",
        document={
            "file_id": "x",
            "file_unique_id": "u",
            "file_name": "doc.pdf",
            "mime_type": "application/pdf",
            "file_size": 100,
        },
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []

    failures = await db_client.get(
        "channel_trigger_audit",
        {"channel": "telegram", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(failures) == 1
