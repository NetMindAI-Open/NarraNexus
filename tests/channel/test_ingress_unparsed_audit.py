"""
@file_name: test_ingress_unparsed_audit.py
@author: Bin Liang
@date: 2026-07-03
@description: Raw events that parse_event rejects must leave an audit trail.

Before this, ``parse_event(raw) -> None`` (stickers / images / voice on
text-only channels) hit a bare ``continue`` — no log, no audit row. When a
user asked "why didn't the bot answer my sticker?" there was nothing to
query (violates CLAUDE.md lessons #3/#5). ``_on_unparsed`` now writes an
``ingress_dropped_unparsed`` audit event with the raw item's shape (keys
only — payloads may be large or sensitive).
"""

import pytest

from xyz_agent_context.channel.channel_audit_events import (
    EVENT_INGRESS_DROPPED_UNPARSED,
)
from xyz_agent_context.module.wechat_module.wechat_trigger import WeChatTrigger
from xyz_agent_context.module.wechat_module._wechat_credential_manager import (
    WeChatCredential,
)


class _CaptureAuditRepo:
    def __init__(self):
        self.rows = []

    async def append(self, event_type, **kwargs):
        self.rows.append((event_type, kwargs))


@pytest.mark.asyncio
async def test_on_unparsed_writes_audit_row():
    trigger = WeChatTrigger()
    repo = _CaptureAuditRepo()
    trigger._audit_repo = repo
    cred = WeChatCredential(agent_id="agent_x", bot_token="t", base_url="", enabled=True)

    await trigger._on_unparsed(cred, {"item_list": [{"image_item": {}}], "from_user_id": "u1"})

    assert len(repo.rows) == 1
    event_type, kwargs = repo.rows[0]
    assert event_type == EVENT_INGRESS_DROPPED_UNPARSED
    assert kwargs["agent_id"] == "agent_x"
    assert "item_list" in kwargs["details"]["raw_keys"]
    # payload values must NOT be shipped into the audit row
    assert "image_item" not in str(kwargs["details"])


@pytest.mark.asyncio
async def test_on_unparsed_survives_missing_audit_repo():
    trigger = WeChatTrigger()
    trigger._audit_repo = None
    cred = WeChatCredential(agent_id="agent_x", bot_token="t", base_url="", enabled=True)
    await trigger._on_unparsed(cred, {"whatever": 1})  # must not raise
