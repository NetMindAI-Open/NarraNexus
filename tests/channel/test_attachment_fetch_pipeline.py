"""
@file_name: test_attachment_fetch_pipeline.py
@date: 2026-05-20
@description: End-to-end integration test for the Phase 1a attachment
ingestion path on ``ChannelTriggerBase``.

A fake trigger subclasses ``ChannelTriggerBase`` and overrides
``fetch_attachments`` to call ``_persist_attachment`` directly with
scripted bytes (no real platform SDK). The test asserts that:

  1. Bytes land at the agent-owner's workspace under
     ``user_upload_files/<today>/att_*.<ext>``.
  2. The ``Attachment.model_dump`` reaches ``collect_run`` inside
     ``trigger_extra_data["attachments"]`` (matches the WS upload
     route's shape in ``backend/routes/websocket.py``).
  3. ``EVENT_ATTACHMENT_PERSISTED`` audit rows are written.
  4. STT is **not** invoked for non-audio MIME (PDF stays
     ``transcript=None``).

Mirrors the test scaffold from ``test_mock_channel_trigger_integration.py``
(scripted events + monkeypatched ``collect_run`` + db_client fixture).
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_ATTACHMENT_PERSISTED,
)
from xyz_agent_context.channel.channel_context_builder_base import (
    ChannelContextBuilderBase,
)
from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase
from xyz_agent_context.schema.attachment_schema import Attachment, AttachmentCategory
from xyz_agent_context.schema.hook_schema import WorkingSource
from xyz_agent_context.schema.parsed_message import (
    MessageContentType,
    ParsedMessage,
)


# ────────────────────────────────────────────────────────────────────
# Fakes
# ────────────────────────────────────────────────────────────────────


@dataclass
class _FakeCredential:
    agent_id: str = "agent_a"
    app_id: str = "fake_bot_1"


class _FakeContextBuilder(ChannelContextBuilderBase):
    def __init__(self, message: ParsedMessage):
        self._m = message

    async def get_message_info(self):
        return {
            "channel_display_name": "Fake",
            "channel_key": "fake",
            "room_name": "",
            "room_id": self._m.chat_id,
            "room_type": "Direct Message",
            "sender_display_name": self._m.sender_name,
            "sender_id": self._m.sender_id,
            "timestamp": str(self._m.timestamp_ms),
            "my_channel_id": "",
            "message_body": self._m.content,
            "send_tool_name": "fake_send",
        }

    async def get_conversation_history(self, limit):
        return []

    async def get_room_members(self):
        return []


class _FakeAttachmentTrigger(ChannelTriggerBase):
    """Drives scripted attachment-bearing events through the base pipeline."""

    channel_name = "fake"
    brand_display = "Fake"
    working_source = WorkingSource.LARK  # any existing value is fine for the fake

    CREDENTIAL_POLL_INTERVAL_SECONDS = 1
    IDLE_POLL_INTERVAL_SECONDS = 1
    PROCESS_MESSAGE_TIMEOUT_SECONDS = 30

    def __init__(self, scripted_events, credential, ref_bytes_map):
        super().__init__(base_workers=2)
        self._scripted = list(scripted_events)
        self._credential = credential
        # platform_ref → raw bytes the test wants to "download"
        self._ref_bytes = ref_bytes_map

    async def load_active_credentials(self):
        return [self._credential]

    async def connect(self, credential):
        for raw in self._scripted:
            yield raw
            await asyncio.sleep(0.02)

    def parse_event(self, raw):
        return ParsedMessage(
            message_id=raw["id"],
            chat_id=raw.get("chat", "C1"),
            sender_id=raw.get("from", "u_alice"),
            sender_name=raw.get("name", "Alice"),
            content=raw.get("content", ""),
            content_type=MessageContentType.FILE if raw.get("attachment_refs") else MessageContentType.TEXT,
            timestamp_ms=raw.get("ts_ms", 1),
            raw={"attachment_refs": raw.get("attachment_refs") or []},
        )

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return f"resolved_{sender_id}"

    def create_context_builder(self, message, credential, agent_id):
        return _FakeContextBuilder(message)

    async def fetch_attachments(self, message, credential):  # type: ignore[override]
        """Test-only fetch: read bytes from the in-memory map and call
        the real ``_persist_attachment`` helper. Exercises MIME sniffing,
        on-disk storage, and Attachment assembly without hitting a real
        platform SDK."""
        refs = (message.raw or {}).get("attachment_refs") or []
        out: list[Attachment] = []
        for ref in refs:
            platform_ref = ref.get("platform_ref")
            raw_bytes = self._ref_bytes.get(platform_ref)
            if raw_bytes is None:
                continue
            att = await self._persist_attachment(
                agent_id=credential.agent_id,
                raw_bytes=raw_bytes,
                original_name=ref["original_name"],
                mime_hint=ref.get("mime_hint", ""),
            )
            out.append(att)
            await self._audit(
                EVENT_ATTACHMENT_PERSISTED,
                message_id=message.message_id,
                agent_id=credential.agent_id,
                app_id=credential.app_id,
                chat_id=message.chat_id,
                sender_id=message.sender_id,
                details={
                    "file_id": att.file_id,
                    "mime_type": att.mime_type,
                    "size_bytes": att.size_bytes,
                },
            )
        return out


# ────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────


async def _wait_for_messages(db_client, channel_id, count, timeout=5.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        rows = await db_client.get("bus_messages", {"channel_id": channel_id})
        if len(rows) >= count:
            return rows
        await asyncio.sleep(0.05)
    return await db_client.get("bus_messages", {"channel_id": channel_id})


@pytest.fixture
def isolated_workspace(monkeypatch, tmp_path: Path) -> Path:
    """Redirect ``base_working_path`` so test attachments don't pollute
    ``~/.nexusagent/workspaces``. Returns the tmp root."""
    from xyz_agent_context import settings as settings_mod

    monkeypatch.setattr(
        settings_mod.settings, "base_working_path", str(tmp_path)
    )
    return tmp_path


# Minimal valid PDF byte stream — libmagic recognises the %PDF- header
# and reports application/pdf regardless of the platform hint we feed.
_FAKE_PDF_BYTES = (
    b"%PDF-1.4\n"
    b"1 0 obj << /Type /Catalog >> endobj\n"
    b"xref\n0 1\n0000000000 65535 f \n"
    b"trailer << /Size 1 >>\nstartxref\n0\n%%EOF\n"
)


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_attachment_persisted_to_disk_and_forwarded_to_agent(
    db_client, monkeypatch, isolated_workspace: Path
):
    """A single attachment-bearing event flows through the pipeline:
    bytes land on disk + Attachment dict reaches ``collect_run``."""
    # Agent owner row → drives the workspace path resolution.
    await db_client.insert("agents", {
        "agent_id": "agent_a",
        "agent_name": "FakeAgent",
        "created_by": "user_owner",
        "is_public": 0,
    })

    # Capture the trigger_extra_data passed to collect_run.
    captured: dict = {}

    async def _capture_collect_run(runtime, **kwargs):
        captured.update(kwargs)

        @dataclass
        class _R:
            output_text: str = "agent reply"
            is_error: bool = False
            error: object = None
            raw_items: list = None

            def __post_init__(self):
                if self.raw_items is None:
                    self.raw_items = []

        return _R()

    class _FakeAgentRuntime:
        def __init__(self, *a, **kw):
            pass

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _capture_collect_run)

    cred = _FakeCredential(agent_id="agent_a", app_id="fake_bot_1")
    scripted = [
        {
            "id": "m1",
            "from": "u_alice",
            "content": "see this report",
            "ts_ms": 9_999_999_999_999,
            "chat": "C1",
            "attachment_refs": [
                {
                    "platform_ref": "ref_pdf_1",
                    "original_name": "report.pdf",
                    "mime_hint": "application/pdf",
                    "size_hint": len(_FAKE_PDF_BYTES),
                }
            ],
        },
    ]
    trigger = _FakeAttachmentTrigger(
        scripted, cred, {"ref_pdf_1": _FAKE_PDF_BYTES}
    )
    await trigger.start(db_client)
    try:
        rows = await _wait_for_messages(
            db_client, "fake_C1", count=2, timeout=5.0
        )
    finally:
        await trigger.stop()

    # 2 bus_messages rows: 1 inbound (user message) + 1 outbound (agent reply).
    assert len(rows) == 2

    # Attachments injected into trigger_extra_data exactly as WS route does.
    extra = captured.get("trigger_extra_data") or {}
    attachments = extra.get("attachments")
    assert attachments is not None, f"missing attachments key; got {extra=}"
    assert len(attachments) == 1
    att = attachments[0]
    # Pydantic model_dump(mode="json") emits the enum as its string value.
    assert att["original_name"] == "report.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["category"] == AttachmentCategory.DOCUMENT.value
    assert att["size_bytes"] == len(_FAKE_PDF_BYTES)
    assert att["transcript"] is None  # PDF, not audio — no STT
    file_id = att["file_id"]
    assert file_id.startswith("att_")

    # Bytes land on disk at the owner's workspace.
    expected_dir = isolated_workspace / agent_workspace_relpath("agent_a", "user_owner") / "user_upload_files"
    assert expected_dir.exists(), f"workspace path {expected_dir} not created"
    pdfs = list(expected_dir.rglob("att_*.pdf"))
    assert len(pdfs) == 1
    assert pdfs[0].read_bytes() == _FAKE_PDF_BYTES

    # Audit row recorded.
    audits = await db_client.get(
        "channel_trigger_audit",
        {"channel": "fake", "event_type": EVENT_ATTACHMENT_PERSISTED},
    )
    assert len(audits) == 1


@pytest.mark.asyncio
async def test_no_attachments_emits_no_attachments_key(
    db_client, monkeypatch, isolated_workspace: Path
):
    """Plain text event → ``trigger_extra_data["attachments"]`` MUST be
    absent (mirrors the WS route's ``{"attachments": ...} if ...`` guard
    in backend/routes/websocket.py:644-648). ChatModule's
    ``.get("attachments")`` check then naturally falls through to
    text-only chat history."""
    await db_client.insert("agents", {
        "agent_id": "agent_a",
        "agent_name": "FakeAgent",
        "created_by": "user_owner",
        "is_public": 0,
    })

    captured: dict = {}

    async def _capture_collect_run(runtime, **kwargs):
        captured.update(kwargs)

        @dataclass
        class _R:
            output_text: str = "ack"
            is_error: bool = False
            error: object = None
            raw_items: list = None

            def __post_init__(self):
                if self.raw_items is None:
                    self.raw_items = []

        return _R()

    class _FakeAgentRuntime:
        def __init__(self, *a, **kw):
            pass

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _capture_collect_run)

    cred = _FakeCredential()
    scripted = [
        {"id": "m1", "from": "u_alice", "content": "just text",
         "ts_ms": 9_999_999_999_999, "chat": "C1"},  # no attachment_refs
    ]
    trigger = _FakeAttachmentTrigger(scripted, cred, {})
    await trigger.start(db_client)
    try:
        await _wait_for_messages(db_client, "fake_C1", count=2, timeout=4.0)
    finally:
        await trigger.stop()

    extra = captured.get("trigger_extra_data") or {}
    assert "attachments" not in extra, (
        f"empty attachments must not be set; got {extra=}"
    )
    # Existing keys preserved.
    assert "channel_tag" in extra
    assert "trigger_id" in extra


@pytest.mark.asyncio
async def test_fetch_attachments_raise_degrades_gracefully(
    db_client, monkeypatch, isolated_workspace: Path
):
    """If ``fetch_attachments`` raises (broken SDK, network down), the
    base catches it, audits ``EVENT_ATTACHMENT_FETCH_FAILED``, and the
    agent still runs with text-only content. Never-raise contract."""
    from xyz_agent_context.channel.channel_audit_events import (
        EVENT_ATTACHMENT_FETCH_FAILED,
    )

    await db_client.insert("agents", {
        "agent_id": "agent_a",
        "agent_name": "FakeAgent",
        "created_by": "user_owner",
        "is_public": 0,
    })

    class _BrokenFetchTrigger(_FakeAttachmentTrigger):
        async def fetch_attachments(self, message, credential):  # type: ignore[override]
            raise RuntimeError("simulated SDK failure")

    captured: dict = {}

    async def _capture_collect_run(runtime, **kwargs):
        captured.update(kwargs)

        @dataclass
        class _R:
            output_text: str = "still replied"
            is_error: bool = False
            error: object = None
            raw_items: list = None

            def __post_init__(self):
                if self.raw_items is None:
                    self.raw_items = []

        return _R()

    class _FakeAgentRuntime:
        def __init__(self, *a, **kw):
            pass

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _capture_collect_run)

    cred = _FakeCredential()
    scripted = [
        {
            "id": "m1",
            "from": "u_alice",
            "content": "see this",
            "ts_ms": 9_999_999_999_999,
            "chat": "C1",
            "attachment_refs": [
                {"platform_ref": "x", "original_name": "x.pdf",
                 "mime_hint": "application/pdf", "size_hint": 100},
            ],
        },
    ]
    trigger = _BrokenFetchTrigger(scripted, cred, {})
    await trigger.start(db_client)
    try:
        rows = await _wait_for_messages(
            db_client, "fake_C1", count=2, timeout=4.0
        )
    finally:
        await trigger.stop()

    # Agent still ran. Inbox has both inbound + outbound rows.
    assert len(rows) == 2
    # No "attachments" key (fetch failed).
    extra = captured.get("trigger_extra_data") or {}
    assert "attachments" not in extra
    # Failure audited.
    fails = await db_client.get(
        "channel_trigger_audit",
        {"channel": "fake", "event_type": EVENT_ATTACHMENT_FETCH_FAILED},
    )
    assert len(fails) == 1


@pytest.mark.asyncio
async def test_caption_less_file_upload_still_processed(
    db_client, monkeypatch, isolated_workspace: Path
):
    """A file upload with **empty content** (no caption text) must still
    trigger the full pipeline. Regression for a Phase 1a oversight where
    base._process_message had ``if not message.content: return`` BEFORE
    fetch_attachments, silently dropping any caption-less file upload.

    Real-world failure mode: Slack drag-drop a PDF without typing
    anything → text="" + files=[...]. Phase 1b parse_event correctly
    extracted attachment_refs, but the base guard cut it off before
    fetch_attachments could ever run.

    Fix: the guard now keeps the early-return only when there's NEITHER
    text NOR attachment_refs.
    """
    await db_client.insert("agents", {
        "agent_id": "agent_a",
        "agent_name": "FakeAgent",
        "created_by": "user_owner",
        "is_public": 0,
    })

    captured: dict = {}

    async def _capture_collect_run(runtime, **kwargs):
        captured.update(kwargs)

        @dataclass
        class _R:
            output_text: str = "I see the file"
            is_error: bool = False
            error: object = None
            raw_items: list = None

            def __post_init__(self):
                if self.raw_items is None:
                    self.raw_items = []

        return _R()

    class _FakeAgentRuntime:
        def __init__(self, *a, **kw):
            pass

    import xyz_agent_context.agent_runtime.agent_runtime as ar_mod
    import xyz_agent_context.agent_runtime.run_collector as rc_mod
    monkeypatch.setattr(ar_mod, "AgentRuntime", _FakeAgentRuntime)
    monkeypatch.setattr(rc_mod, "collect_run", _capture_collect_run)

    cred = _FakeCredential(agent_id="agent_a", app_id="fake_bot_1")
    # No "content" field on the event → ParsedMessage.content == ""
    # But attachment_refs is non-empty → must still flow.
    scripted = [
        {
            "id": "m_caption_less",
            "from": "u_alice",
            "content": "",   # ← KEY: empty caption
            "ts_ms": 9_999_999_999_999,
            "chat": "C1",
            "attachment_refs": [
                {
                    "platform_ref": "ref_pdf_nocap",
                    "original_name": "report.pdf",
                    "mime_hint": "application/pdf",
                    "size_hint": len(_FAKE_PDF_BYTES),
                }
            ],
        },
    ]
    trigger = _FakeAttachmentTrigger(
        scripted, cred, {"ref_pdf_nocap": _FAKE_PDF_BYTES}
    )
    await trigger.start(db_client)
    try:
        rows = await _wait_for_messages(
            db_client, "fake_C1", count=2, timeout=5.0
        )
    finally:
        await trigger.stop()

    # 2 rows: inbound + outbound — the caption-less file DID trigger the agent.
    assert len(rows) == 2
    extra = captured.get("trigger_extra_data") or {}
    attachments = extra.get("attachments")
    assert attachments is not None and len(attachments) == 1, (
        f"caption-less file must still surface attachments; got {extra=}"
    )
    assert attachments[0]["original_name"] == "report.pdf"
    # File is on disk.
    workspace = isolated_workspace / agent_workspace_relpath("agent_a", "user_owner") / "user_upload_files"
    pdfs = list(workspace.rglob("att_*.pdf"))
    assert len(pdfs) == 1
