"""
@file_name: test_lark_attachment_ingest.py
@date: 2026-05-21
@description: Phase 1c T20 — end-to-end attachment ingest tests for Lark.

Covers ``LarkTrigger.parse_event`` (files-array → attachment_refs
extraction across media message_types) and ``fetch_attachments``
(lark-cli resource fetch → ``_persist_attachment`` → audit).

The lark-cli subprocess is monkey-patched per test (no real network);
we focus on the trigger's contract:
  - parse_event populates ``raw["attachment_refs"]`` for image/file/
    audio/media and skips text/post/sticker
  - fetch_attachments audits oversized / fetch_failed / persisted
  - never-raise: per-ref failures degrade gracefully
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_FETCH_FAILED,
    EVENT_ATTACHMENT_PERSISTED,
    EVENT_INGRESS_DROPPED_OVERSIZED,
)
from xyz_agent_context.module.lark_module._lark_credential_manager import (
    LarkCredential,
)
from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger
from xyz_agent_context.schema.attachment_schema import AttachmentCategory


# ─────────────────────────────────────────────────────────────────────
# Fixtures + helpers
# ─────────────────────────────────────────────────────────────────────


def _cred(agent_id: str = "agent_test") -> LarkCredential:
    """Minimal credential — only fields fetch_attachments / audit touches."""
    return LarkCredential(
        agent_id=agent_id,
        app_id="cli_test",
        app_secret_ref="appsecret:cli_test",
        brand="lark",
        profile_name="testbot",
        workspace_path="",
    )


def _raw(
    *, message_type: str, content_payload: dict, msg_id: str = "om_msg_1"
) -> dict:
    return {
        "message_id": msg_id,
        "chat_id": "oc_chat",
        "sender_id": "ou_sender",
        "sender_name": "Tong",
        "message_type": message_type,
        "content": json.dumps(content_payload, ensure_ascii=False),
        "create_time": "1779349247000",
    }


def _trigger_with_audit() -> tuple[LarkTrigger, AsyncMock]:
    """Build a trigger with a mocked audit repo so we can assert events."""
    t = LarkTrigger()
    # _audit() guards on self._audit_repo being None; install a mock.
    audit_repo = AsyncMock()
    audit_repo.append = AsyncMock()
    t._audit_repo = audit_repo
    return t, audit_repo


def _audit_event_types(audit_repo: AsyncMock) -> list[str]:
    """Pull positional event_type args from every audit_repo.append call."""
    return [c.args[0] for c in audit_repo.append.call_args_list]


# ─────────────────────────────────────────────────────────────────────
# parse_event → attachment_refs extraction
# ─────────────────────────────────────────────────────────────────────


def test_image_message_produces_image_attachment_ref() -> None:
    parsed = LarkTrigger().parse_event(
        _raw(
            message_type="image",
            content_payload={"image_key": "img_v3_zzz"},
            msg_id="om_x",
        )
    )
    assert parsed is not None
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    r = refs[0]
    assert r["kind"] == "image"
    assert r["platform_ref"] == "img_v3_zzz"
    assert r["lark_resource_type"] == "image"
    assert r["lark_message_id"] == "om_x"
    assert r["mime_hint"] == "image/png"


def test_file_message_produces_file_attachment_ref_with_size() -> None:
    parsed = LarkTrigger().parse_event(
        _raw(
            message_type="file",
            content_payload={
                "file_key": "file_v3_yyy",
                "file_name": "report.pdf",
                "file_size": 154823,
            },
            msg_id="om_y",
        )
    )
    assert parsed is not None
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    r = refs[0]
    assert r["kind"] == "file"
    assert r["platform_ref"] == "file_v3_yyy"
    assert r["original_name"] == "report.pdf"
    assert r["size_hint"] == 154823
    assert r["lark_resource_type"] == "file"


def test_audio_message_produces_audio_attachment_ref() -> None:
    parsed = LarkTrigger().parse_event(
        _raw(
            message_type="audio",
            content_payload={"file_key": "audio_xx", "duration": 5400},
            msg_id="om_a",
        )
    )
    assert parsed is not None
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    assert refs[0]["kind"] == "audio"
    assert refs[0]["lark_resource_type"] == "audio"


def test_media_message_produces_media_attachment_ref() -> None:
    parsed = LarkTrigger().parse_event(
        _raw(
            message_type="media",
            content_payload={
                "file_key": "media_xx",
                "file_name": "demo.mp4",
                "duration": 60000,
            },
            msg_id="om_m",
        )
    )
    assert parsed is not None
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    assert refs[0]["kind"] == "media"
    assert refs[0]["lark_resource_type"] == "media"
    assert refs[0]["original_name"] == "demo.mp4"


def test_text_message_produces_no_attachment_refs() -> None:
    parsed = LarkTrigger().parse_event(
        _raw(message_type="text", content_payload={"text": "hello"})
    )
    assert parsed is not None
    assert parsed.raw.get("attachment_refs", []) == []


def test_post_message_produces_no_attachment_refs() -> None:
    payload = {"zh_cn": {"title": "t", "content": [[{"tag": "text", "text": "x"}]]}}
    parsed = LarkTrigger().parse_event(
        _raw(message_type="post", content_payload=payload)
    )
    assert parsed is not None
    assert parsed.raw.get("attachment_refs", []) == []


def test_sticker_message_produces_no_attachment_refs() -> None:
    """Stickers are platform assets, not user uploads. Skip."""
    parsed = LarkTrigger().parse_event(
        _raw(message_type="sticker", content_payload={"file_key": "stk_xx"})
    )
    assert parsed is not None
    assert parsed.raw.get("attachment_refs", []) == []


def test_image_message_with_missing_image_key_produces_no_refs() -> None:
    """Defensive: malformed payloads must not crash and must not produce a
    ref pointing at empty string."""
    parsed = LarkTrigger().parse_event(
        _raw(message_type="image", content_payload={"not_image_key": "x"})
    )
    assert parsed is not None
    assert parsed.raw.get("attachment_refs", []) == []


def test_file_message_with_missing_file_size_defaults_to_zero() -> None:
    parsed = LarkTrigger().parse_event(
        _raw(
            message_type="file",
            content_payload={"file_key": "fk", "file_name": "x.pdf"},
        )
    )
    assert parsed is not None
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    assert refs[0]["size_hint"] == 0


# ─────────────────────────────────────────────────────────────────────
# fetch_attachments — happy path, errors, oversized, partial success
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fetch_attachments_empty_refs_returns_empty_list() -> None:
    t = LarkTrigger()
    parsed = t.parse_event(_raw(message_type="text", content_payload={"text": "hi"}))
    assert parsed is not None
    got = await t.fetch_attachments(parsed, _cred())
    assert got == []


@pytest.mark.asyncio
async def test_fetch_attachments_downloads_and_persists_pdf(monkeypatch, tmp_path) -> None:
    """End-to-end with mocked lark-cli + base helper."""
    t, audit = _trigger_with_audit()

    parsed = t.parse_event(
        _raw(
            message_type="file",
            content_payload={
                "file_key": "file_v3_pdfkey",
                "file_name": "spec.pdf",
                "file_size": 5000,
            },
            msg_id="om_pdf",
        )
    )
    assert parsed is not None

    # Mock lark-cli to return bytes without spawning subprocess.
    fake_pdf_bytes = b"%PDF-1.4 fake pdf body"
    async def fake_fetch(agent_id, *, message_id, file_key, resource_type, timeout=60.0):
        assert agent_id == "agent_test"
        assert message_id == "om_pdf"
        assert file_key == "file_v3_pdfkey"
        assert resource_type == "file"
        return fake_pdf_bytes
    monkeypatch.setattr(t._cli, "fetch_message_resource", fake_fetch)

    # Mock _persist_attachment to avoid filesystem writes + workspace setup.
    async def fake_persist(*, agent_id, raw_bytes, original_name, mime_hint):
        from xyz_agent_context.schema.attachment_schema import (
            Attachment, AttachmentCategory,
        )
        return Attachment(
            file_id="att_fake",
            mime_type="application/pdf",
            original_name=original_name,
            size_bytes=len(raw_bytes),
            category=AttachmentCategory.DOCUMENT,
            transcript=None,
        )
    monkeypatch.setattr(t, "_persist_attachment", fake_persist)

    out = await t.fetch_attachments(parsed, _cred())
    assert len(out) == 1
    att = out[0]
    assert att.original_name == "spec.pdf"
    assert att.size_bytes == len(fake_pdf_bytes)
    assert att.mime_type == "application/pdf"
    assert att.category == AttachmentCategory.DOCUMENT
    assert EVENT_ATTACHMENT_PERSISTED in _audit_event_types(audit)


@pytest.mark.asyncio
async def test_fetch_attachments_oversized_pre_check_audits_and_skips(monkeypatch) -> None:
    """size_hint > backend max_upload_bytes → audit DROPPED_OVERSIZED, no download."""
    t, audit = _trigger_with_audit()

    # Force a tiny cap.
    from backend.config import settings as backend_settings
    monkeypatch.setattr(backend_settings, "max_upload_bytes", 1024)

    parsed = t.parse_event(
        _raw(
            message_type="file",
            content_payload={
                "file_key": "fk_huge",
                "file_name": "huge.pdf",
                "file_size": 999_999,
            },
        )
    )
    assert parsed is not None

    called = {"fetch": False}
    async def fake_fetch(*args, **kwargs):
        called["fetch"] = True
        raise AssertionError("download must not be attempted for oversized refs")
    monkeypatch.setattr(t._cli, "fetch_message_resource", fake_fetch)

    out = await t.fetch_attachments(parsed, _cred())
    assert out == []
    assert called["fetch"] is False
    assert EVENT_INGRESS_DROPPED_OVERSIZED in _audit_event_types(audit)


@pytest.mark.asyncio
async def test_fetch_attachments_post_download_cap_audits_and_skips(monkeypatch) -> None:
    """size_hint=0 in event, but downloaded bytes exceed cap → audit + skip."""
    t, audit = _trigger_with_audit()

    from backend.config import settings as backend_settings
    monkeypatch.setattr(backend_settings, "max_upload_bytes", 100)

    parsed = t.parse_event(
        _raw(
            message_type="audio",
            content_payload={"file_key": "audio_x", "duration": 5400},
        )
    )
    assert parsed is not None

    async def fake_fetch(*args, **kwargs):
        return b"x" * 500  # exceeds the 100B cap

    monkeypatch.setattr(t._cli, "fetch_message_resource", fake_fetch)

    persisted = {"called": False}
    async def fake_persist(**kwargs):
        persisted["called"] = True
        raise AssertionError("persist must not be called for oversized download")
    monkeypatch.setattr(t, "_persist_attachment", fake_persist)

    out = await t.fetch_attachments(parsed, _cred())
    assert out == []
    assert persisted["called"] is False
    assert EVENT_INGRESS_DROPPED_OVERSIZED in _audit_event_types(audit)


@pytest.mark.asyncio
async def test_fetch_attachments_cli_failure_audits_and_skips(monkeypatch) -> None:
    """lark-cli raises → audit ATTACHMENT_FETCH_FAILED, continue with next ref."""
    t, audit = _trigger_with_audit()

    parsed = t.parse_event(
        _raw(
            message_type="file",
            content_payload={"file_key": "fk_perm_denied", "file_name": "x.pdf"},
        )
    )
    assert parsed is not None

    async def fake_fetch(*args, **kwargs):
        raise RuntimeError("permission denied: missing scope im:resource")
    monkeypatch.setattr(t._cli, "fetch_message_resource", fake_fetch)

    out = await t.fetch_attachments(parsed, _cred())
    assert out == []
    assert EVENT_ATTACHMENT_FETCH_FAILED in _audit_event_types(audit)


@pytest.mark.asyncio
async def test_fetch_attachments_persist_failure_audits_and_skips(monkeypatch) -> None:
    t, audit = _trigger_with_audit()
    parsed = t.parse_event(
        _raw(
            message_type="image",
            content_payload={"image_key": "img_x"},
        )
    )
    assert parsed is not None

    async def fake_fetch(*args, **kwargs):
        return b"\x89PNG fake png"
    monkeypatch.setattr(t._cli, "fetch_message_resource", fake_fetch)

    async def fake_persist(**kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(t, "_persist_attachment", fake_persist)

    out = await t.fetch_attachments(parsed, _cred())
    assert out == []
    fail_audits = [e for e in _audit_event_types(audit) if e == EVENT_ATTACHMENT_FETCH_FAILED]
    assert fail_audits, "persist failure must produce a fetch_failed audit row"


@pytest.mark.asyncio
async def test_fetch_attachments_never_raises_even_on_cli_exception(monkeypatch) -> None:
    """Contract: fetch_attachments returns [] (with audit) rather than
    propagating exceptions, so the agent run can still happen text-only."""
    t, _ = _trigger_with_audit()
    parsed = t.parse_event(
        _raw(message_type="file", content_payload={"file_key": "fk"})
    )
    assert parsed is not None

    async def fake_fetch(*args, **kwargs):
        raise RuntimeError("boom")
    monkeypatch.setattr(t._cli, "fetch_message_resource", fake_fetch)

    # Must not raise.
    out = await t.fetch_attachments(parsed, _cred())
    assert out == []


# ─────────────────────────────────────────────────────────────────────
# M3 regression: attachments → trigger_extra_data wiring
# Lark fully overrides _build_and_run_agent, so the base's attachment
# injection logic does NOT run on Lark paths. Pin the Lark-side wiring
# so a future refactor can't silently break the contract.
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attachments_wired_into_trigger_extra_data(monkeypatch, tmp_path):
    """A non-empty ``attachments`` kwarg into ``_build_and_run_agent``
    MUST populate ``trigger_extra_data["attachments"]`` with the
    ``model_dump(mode="json")`` of each Attachment. The receiver
    (ChatModule) keys on this exact dict shape — drift here would
    silently break the attachment marker injection in chat_history.
    """
    from dataclasses import dataclass
    from xyz_agent_context.schema.attachment_schema import (
        Attachment, AttachmentCategory,
    )
    from xyz_agent_context.schema.parsed_message import ParsedMessage

    # Redirect workspace so we don't write outside tmp.
    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "base_working_path", str(tmp_path)
    )

    # Capture trigger_extra_data forwarded to collect_run.
    captured: dict = {}

    async def _capture_collect_run(runtime, **kwargs):
        captured.update(kwargs)

        @dataclass
        class _R:
            output_text: str = ""
            is_error: bool = False
            error: object = None
            raw_items: list = None

            def __post_init__(self):
                if self.raw_items is None:
                    self.raw_items = []

        return _R()

    class _FakeRuntime:
        def __init__(self, *a, **kw):
            pass

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _capture_collect_run)
    # Lark imports collect_run at module top, also patch the local symbol.
    import xyz_agent_context.module.lark_module.lark_trigger as lt_mod
    monkeypatch.setattr(lt_mod, "collect_run", _capture_collect_run)

    # Avoid making real network calls / DB queries in the builder path.
    async def _fake_resolve_owner(self, agent_id):
        return "user_test_owner"

    monkeypatch.setattr(
        "xyz_agent_context.channel.channel_trigger_base.ChannelTriggerBase._resolve_agent_owner",
        _fake_resolve_owner,
    )

    # Bypass the real context builder — return a tiny prompt.
    class _FakeBuilder:
        async def build_prompt(self, _cfg):
            return "fake prompt"

    t = LarkTrigger()
    monkeypatch.setattr(t, "create_context_builder", lambda *a, **kw: _FakeBuilder())

    parsed = ParsedMessage(
        message_id="om_test_msg",
        chat_id="oc_test_chat",
        sender_id="ou_test_sender",
        sender_name="Test Sender",
        content="caption text",
        timestamp_ms=1700000000000,
        raw={},
    )
    fake_atts = [
        Attachment(
            file_id="att_abcdef01",
            mime_type="application/pdf",
            original_name="report.pdf",
            size_bytes=12345,
            category=AttachmentCategory.DOCUMENT,
        ),
        Attachment(
            file_id="att_abcdef02",
            mime_type="image/jpeg",
            original_name="photo.jpg",
            size_bytes=6789,
            category=AttachmentCategory.IMAGE,
        ),
    ]

    await t._build_and_run_agent(
        _cred(),
        message=parsed,
        sender_name="Test Sender",
        attachments=fake_atts,
    )

    # collect_run was called with trigger_extra_data containing attachments.
    extra = captured.get("trigger_extra_data") or {}
    assert "attachments" in extra, (
        f"trigger_extra_data must carry 'attachments' when non-empty list "
        f"passed. Got: {extra=}"
    )
    dumped = extra["attachments"]
    assert isinstance(dumped, list) and len(dumped) == 2

    # Each entry MUST be the model_dump(mode="json") dict — enum values
    # serialized as strings (not enum instances), this matches the
    # WS upload route shape that ChatModule consumes.
    assert dumped[0]["file_id"] == "att_abcdef01"
    assert dumped[0]["category"] == AttachmentCategory.DOCUMENT.value
    assert dumped[1]["file_id"] == "att_abcdef02"
    assert dumped[1]["category"] == AttachmentCategory.IMAGE.value


@pytest.mark.asyncio
async def test_empty_attachments_does_NOT_set_trigger_extra_data_key(
    monkeypatch, tmp_path,
):
    """When ``attachments`` is empty or None, the key MUST NOT appear in
    ``trigger_extra_data``. Matches the WS / base / Slack pattern so
    ChatModule's ``ctx_data.extra_data.get("attachments")`` returns
    None and chat_history flows text-only.
    """
    from dataclasses import dataclass
    from xyz_agent_context.schema.parsed_message import ParsedMessage

    from xyz_agent_context import settings as settings_mod
    monkeypatch.setattr(
        settings_mod.settings, "base_working_path", str(tmp_path)
    )

    captured: dict = {}

    async def _capture_collect_run(runtime, **kwargs):
        captured.update(kwargs)

        @dataclass
        class _R:
            output_text: str = ""
            is_error: bool = False
            error: object = None
            raw_items: list = None

            def __post_init__(self):
                if self.raw_items is None:
                    self.raw_items = []

        return _R()

    class _FakeRuntime:
        def __init__(self, *a, **kw):
            pass

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    import xyz_agent_context.module.lark_module.lark_trigger as lt_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _capture_collect_run)
    monkeypatch.setattr(lt_mod, "collect_run", _capture_collect_run)

    async def _fake_resolve_owner(self, agent_id):
        return "user_test_owner"

    monkeypatch.setattr(
        "xyz_agent_context.channel.channel_trigger_base.ChannelTriggerBase._resolve_agent_owner",
        _fake_resolve_owner,
    )

    class _FakeBuilder:
        async def build_prompt(self, _cfg):
            return "fake prompt"

    t = LarkTrigger()
    monkeypatch.setattr(t, "create_context_builder", lambda *a, **kw: _FakeBuilder())

    parsed = ParsedMessage(
        message_id="om_text_only",
        chat_id="oc_test",
        sender_id="ou_test",
        sender_name="Test",
        content="hello",
        timestamp_ms=1700000000000,
        raw={},
    )

    # Pass empty list — must NOT set "attachments" key.
    await t._build_and_run_agent(
        _cred(),
        message=parsed,
        sender_name="Test",
        attachments=[],
    )
    extra = captured.get("trigger_extra_data") or {}
    assert "attachments" not in extra, (
        f"empty attachments list must NOT set the key. Got: {extra=}"
    )

    # Same with None.
    captured.clear()
    await t._build_and_run_agent(
        _cred(),
        message=parsed,
        sender_name="Test",
        attachments=None,
    )
    extra = captured.get("trigger_extra_data") or {}
    assert "attachments" not in extra, (
        f"None attachments must NOT set the key. Got: {extra=}"
    )
