"""
@file_name: test_slack_attachment_ingest.py
@date: 2026-05-21
@description: Phase 1b — end-to-end attachment ingest tests for
``SlackTrigger.parse_event`` (files[] extraction) and
``fetch_attachments`` (SDK download → ``_persist_attachment`` → audit).

The SDK download method is monkey-patched per test so no real network
is touched; we focus on the trigger's contract:
  - parse_event populates ``raw["attachment_refs"]`` from ``files[]``
  - parse_event derives the right ``content_type`` from primary file's mime
  - parse_event keeps text-only path unaffected (regression check)
  - parse_event drops ``file_share`` subtype to avoid double-process
    (canonical delivery is the regular ``message`` event with ``files``)
  - parse_event drops messages with no text AND no refs
  - fetch_attachments hydrates missing url_private via files.info
  - fetch_attachments audits oversized / fetch_failed / persisted
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
)
from xyz_agent_context.module.slack_module._slack_credential_manager import (
    SlackCredential,
)
from xyz_agent_context.module.slack_module.slack_sdk_client import (
    SlackSDKError,
)
from xyz_agent_context.module.slack_module.slack_trigger import (
    SlackTrigger,
)
from xyz_agent_context.schema.attachment_schema import AttachmentCategory
from xyz_agent_context.schema.parsed_message import MessageContentType


_FAKE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog >> endobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer << /Size 1 >>\nstartxref\n0\n%%EOF\n"
)


def _cred() -> SlackCredential:
    return SlackCredential(
        agent_id="agent_a",
        bot_token="xoxb-test",
        app_token="xapp-test",
        bot_user_id="U0BOT",
        team_id="T1",
        team_name="Team",
    )


def _dm_event(**overrides) -> dict:
    """Minimal Slack DM message event payload."""
    base = {
        "type": "message",
        "channel_type": "im",
        "channel": "D123",
        "user": "U42",
        "ts": "1700000000.000100",
        "client_msg_id": "uuid-aaa",
        "text": "",
    }
    base.update(overrides)
    return base


def _file(**overrides) -> dict:
    base = {
        "id": "F123",
        "name": "report.pdf",
        "mimetype": "application/pdf",
        "size": len(_FAKE_PDF),
        "url_private": "https://files.slack.com/files-pri/T1-F123/report.pdf",
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────────────
# parse_event — refs extraction
# ────────────────────────────────────────────────────────────────────


def test_parse_event_files_array_creates_refs() -> None:
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(
        text="see this report",
        files=[_file()],
    ))
    assert parsed is not None
    assert parsed.content == "see this report"
    assert parsed.content_type == MessageContentType.FILE
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    ref = refs[0]
    assert ref["platform_ref"] == "F123"
    assert ref["original_name"] == "report.pdf"
    assert ref["mime_hint"] == "application/pdf"
    assert ref["size_hint"] == len(_FAKE_PDF)
    assert ref["url_private"].startswith("https://")


def test_parse_event_image_mime_sets_image_content_type() -> None:
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(
        text="",
        files=[_file(name="photo.jpg", mimetype="image/jpeg")],
    ))
    assert parsed is not None
    assert parsed.content_type == MessageContentType.IMAGE


def test_parse_event_audio_mime_sets_audio_content_type() -> None:
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(
        text="",
        files=[_file(name="memo.m4a", mimetype="audio/mp4")],
    ))
    assert parsed is not None
    assert parsed.content_type == MessageContentType.AUDIO


def test_parse_event_multiple_files_all_captured() -> None:
    """Drag-drop multi-file upload: each file becomes its own ref in order."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(
        text="batch upload",
        files=[
            _file(id="F1", name="a.pdf", mimetype="application/pdf"),
            _file(id="F2", name="b.jpg", mimetype="image/jpeg"),
            _file(id="F3", name="c.csv", mimetype="text/csv"),
        ],
    ))
    assert parsed is not None
    refs = parsed.raw["attachment_refs"]
    assert [r["platform_ref"] for r in refs] == ["F1", "F2", "F3"]
    # Primary (first) is PDF → content_type FILE
    assert parsed.content_type == MessageContentType.FILE


def test_parse_event_text_only_message_unchanged() -> None:
    """Regression: pure-text messages still parse without refs."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(text="hi"))
    assert parsed is not None
    assert parsed.content == "hi"
    assert parsed.content_type == MessageContentType.TEXT
    assert "attachment_refs" not in (parsed.raw or {})


def test_parse_event_empty_text_and_no_files_dropped() -> None:
    """No text + no files = nothing actionable. Return None."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(text=""))
    assert parsed is None


def test_parse_event_file_share_subtype_now_processed() -> None:
    """``file_share`` subtype is the CANONICAL Slack delivery path for DM
    file uploads. Earlier Phase 1b assumption (that there's a separate
    "plain message.im + files[]" path) was wrong — real Slack workspace
    smoke showed text-only "hi" went through but text + file produced
    ZERO audit events. The file_share envelope IS the only delivery.
    This test pins the corrected behaviour: file_share subtype with
    files[] must parse fully, refs populated."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(
        subtype="file_share",
        text="check this report",
        files=[_file()],
    ))
    assert parsed is not None
    assert parsed.content == "check this report"
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    assert refs[0]["platform_ref"] == "F123"


def test_parse_event_files_with_malformed_entry_skipped() -> None:
    """One bad file entry doesn't tank the whole event."""
    trigger = SlackTrigger()
    parsed = trigger.parse_event(_dm_event(
        text="mixed",
        files=[
            "not a dict",            # malformed
            {"name": "no-id.pdf"},   # missing id
            _file(),                  # good
        ],
    ))
    assert parsed is not None
    refs = parsed.raw["attachment_refs"]
    assert len(refs) == 1
    assert refs[0]["platform_ref"] == "F123"


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
            "agent_id": "agent_a",
            "agent_name": "FakeAgent",
            "created_by": "user_owner",
            "is_public": 0,
        })
        trigger = SlackTrigger()
        trigger._db = db_client
        from xyz_agent_context.repository.channel_trigger_audit_repository import (
            ChannelTriggerAuditRepository,
        )
        trigger._audit_repo = ChannelTriggerAuditRepository("slack", db_client)
        return trigger
    return _setup


@pytest.mark.asyncio
async def test_fetch_attachments_downloads_and_persists_pdf(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """Full ingest path: ref → download_url → _persist_attachment → audit."""
    trigger = await trigger_with_owner()
    cred = _cred()

    class _StubSDK:
        async def files_info(self, file_id):  # Should NOT be called when url_private present
            raise AssertionError("files_info should be skipped when url_private is set")

        async def download_url(self, url, *, max_bytes):
            assert url.endswith("/report.pdf")
            return _FAKE_PDF

    import xyz_agent_context.module.slack_module.slack_trigger as st_mod
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda *_a, **_kw: _StubSDK())

    parsed = trigger.parse_event(_dm_event(
        text="see report",
        files=[_file()],
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert len(attachments) == 1
    att = attachments[0]
    assert att.original_name == "report.pdf"
    assert att.mime_type == "application/pdf"
    assert att.category == AttachmentCategory.DOCUMENT
    assert att.transcript is None

    # Bytes on disk under OWNER's workspace
    workspace = isolated_workspace / "agent_a_user_owner" / "user_upload_files"
    assert any(p.read_bytes() == _FAKE_PDF for p in workspace.rglob("att_*.pdf"))

    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "slack", "event_type": EVENT_ATTACHMENT_PERSISTED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_hydrates_missing_url_via_files_info(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """When the event lacks url_private, fetch_attachments calls files.info
    to recover the canonical URL before downloading."""
    trigger = await trigger_with_owner()
    cred = _cred()

    files_info_called = []

    class _StubSDK:
        async def files_info(self, file_id):
            files_info_called.append(file_id)
            return {
                "id": file_id,
                "name": "report.pdf",
                "mimetype": "application/pdf",
                "size": len(_FAKE_PDF),
                "url_private": "https://files.slack.com/recovered/report.pdf",
            }

        async def download_url(self, url, *, max_bytes):
            assert url == "https://files.slack.com/recovered/report.pdf"
            return _FAKE_PDF

    import xyz_agent_context.module.slack_module.slack_trigger as st_mod
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda *_a, **_kw: _StubSDK())

    parsed = trigger.parse_event(_dm_event(
        text="",
        files=[_file(url_private="")],  # explicitly missing
    ))
    assert parsed is not None
    refs = parsed.raw["attachment_refs"]
    assert refs[0]["url_private"] == ""  # confirm setup

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert files_info_called == ["F123"]
    assert len(attachments) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_oversized_pre_check(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """size_hint > backend cap → audit EVENT_INGRESS_DROPPED_OVERSIZED
    BEFORE any HTTP call."""
    trigger = await trigger_with_owner()
    cred = _cred()

    download_called = []

    class _StubSDK:
        async def files_info(self, file_id):
            return {}

        async def download_url(self, url, *, max_bytes):
            download_called.append(url)
            return b"should never run"

    import xyz_agent_context.module.slack_module.slack_trigger as st_mod
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda *_a, **_kw: _StubSDK())

    from backend.config import settings as backend_settings
    monkeypatch.setattr(backend_settings, "max_upload_bytes", 1024)

    parsed = trigger.parse_event(_dm_event(
        text="",
        files=[_file(size=2048)],
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []
    assert download_called == []

    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "slack", "event_type": EVENT_INGRESS_DROPPED_OVERSIZED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_download_failure_audited(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """Network error during download → EVENT_ATTACHMENT_FETCH_FAILED."""
    trigger = await trigger_with_owner()
    cred = _cred()

    class _StubSDK:
        async def files_info(self, file_id):
            return {}

        async def download_url(self, url, *, max_bytes):
            raise SlackSDKError("client_error:ClientConnectionError", "boom")

    import xyz_agent_context.module.slack_module.slack_trigger as st_mod
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda *_a, **_kw: _StubSDK())

    parsed = trigger.parse_event(_dm_event(
        text="",
        files=[_file()],
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert attachments == []

    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "slack", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_fetch_attachments_partial_success(
    db_client, isolated_workspace, monkeypatch, trigger_with_owner
):
    """Multi-file message: one fails, others succeed. Partial result returned."""
    trigger = await trigger_with_owner()
    cred = _cred()

    class _StubSDK:
        async def files_info(self, file_id):
            return {}

        async def download_url(self, url, *, max_bytes):
            if "fail" in url:
                raise SlackSDKError("http_500", "server error")
            return _FAKE_PDF

    import xyz_agent_context.module.slack_module.slack_trigger as st_mod
    monkeypatch.setattr(st_mod, "SlackSDKClient", lambda *_a, **_kw: _StubSDK())

    parsed = trigger.parse_event(_dm_event(
        text="batch",
        files=[
            _file(id="F1", url_private="https://files.slack.com/ok-1.pdf"),
            _file(id="F2", url_private="https://files.slack.com/fail-2.pdf"),
            _file(id="F3", url_private="https://files.slack.com/ok-3.pdf"),
        ],
    ))
    assert parsed is not None

    attachments = await trigger.fetch_attachments(parsed, cred)
    assert len(attachments) == 2  # F1, F3 succeeded; F2 failed

    persisted = await db_client.get(
        "channel_trigger_audit",
        {"channel": "slack", "event_type": EVENT_ATTACHMENT_PERSISTED},
    )
    failed = await db_client.get(
        "channel_trigger_audit",
        {"channel": "slack", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(persisted) == 2
    assert len(failed) == 1
