"""
@file_name: test_matrix_compound_ingest.py
@date: 2026-07-03
@description: NarraMessenger "compound message" ingest for MatrixTrigger.

NarraMessenger does NOT deliver multimodal as standard inline m.image
events. A picture/file arrives as a *compound message*: a plain
``m.text`` event whose custom ``content["ai.netmind.hint"]`` carries
``kind="compound_trigger"`` + a ``compound_preview`` holding the REAL
user text and the media ``mxc://`` URL. (A sibling ``ai.netmind.compound``
event carries the raw media but nio parses it as RoomMessageUnknown — we
ignore it; the trigger's preview already has everything.)

Locks:
  - _wrap_event lifts the compound_trigger into a media raw dict using the
    preview's text + media_url + mime + filename (NOT the hidden
    "[internal hint] process compound …" body).
  - parse_event turns it into a ParsedMessage whose content is the real
    user text and whose raw["attachment_refs"] points at the mxc, so the
    existing fetch_attachments pipeline downloads it.
  - a plain text event (no hint) is unchanged.
"""
from __future__ import annotations

from typing import Any

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module.matrix_trigger import (
    MatrixTrigger,
)
from xyz_agent_context.schema.parsed_message import MessageContentType

HOMESERVER = "matrix.netmind.chat"
AGENT_MXID = f"@agent-88956f5b:{HOMESERVER}"
SENDER = f"@sp-61953ceb1b6b46d5:{HOMESERVER}"
MXC = f"mxc://{HOMESERVER}/xzwIYfaOPyvyISpJjOcfhdar"


def _cred(**o) -> NarramessengerCredential:
    base = dict(
        agent_id="agent_62cf67080ad4",
        bearer_token="tok",
        matrix_homeserver_url=f"https://{HOMESERVER}",
        matrix_user_id=AGENT_MXID,
        matrix_access_token="syt_fake",
    )
    base.update(o)
    return NarramessengerCredential(**base)


def _compound_text_event(
    *,
    text: str = "what do you think of this advertisement picture?",
    media_url: str = MXC,
    mime: str = "image/png",
    file_name: str = "1280X1280.PNG",
    event_id: str = "$iMJxdQLYGp59E9ZrYFNoTqwH_Pp_PoXh8h32tb5LdYg",
) -> Any:
    """Real nio RoomMessageText carrying NarraMessenger's compound_trigger
    hint in its raw ``source`` content (exactly as observed on the wire)."""
    from nio import RoomMessageText

    preview: dict[str, Any] = {}
    if text:
        preview["text"] = text
    if media_url:
        preview["media_url"] = media_url
    if mime:
        preview["mime_type"] = mime
    if file_name:
        preview["file_name"] = file_name
    content = {
        "ai.netmind.hint": {
            "kind": "compound_trigger",
            "version": 1,
            "target_event_id": "$bUsWYLI0V9XX4cEqSJ4hoN2pp8mss_D9RWHjmFSpExY",
            "target_msgtype": "ai.netmind.compound",
            "compound_preview": preview,
        },
        "ai.netmind.visibility": {"status": "hidden", "reason": "agent_internal_hint"},
        "m.mentions": {"user_ids": [AGENT_MXID]},
        "msgtype": "m.text",
        "body": "[internal hint] process compound $bUsWYLI0V9XX4cEqSJ4hoN2pp8mss_D9RWHjmFSpExY",
    }
    ev = RoomMessageText.__new__(RoomMessageText)
    ev.event_id = event_id
    ev.sender = SENDER
    ev.server_timestamp = 1783079391632
    ev.body = content["body"]
    ev.source = {"content": content}
    return ev


def _plain_text_event(body: str = "just text") -> Any:
    from nio import RoomMessageText

    ev = RoomMessageText.__new__(RoomMessageText)
    ev.event_id = "$plain"
    ev.sender = SENDER
    ev.server_timestamp = 1
    ev.body = body
    ev.source = {"content": {"msgtype": "m.text", "body": body}}
    return ev


# ── _wrap_event ─────────────────────────────────────────────────────────
def test_wrap_event_lifts_compound_trigger():
    raw = MatrixTrigger()._wrap_event(
        event=_compound_text_event(), room_id="!wsorpuc:h", credential=_cred()
    )
    assert raw is not None
    assert raw["kind"] == "m.room.message.compound"
    assert raw["text"] == "what do you think of this advertisement picture?"
    assert raw["mxc_url"] == MXC
    assert raw["mimetype"] == "image/png"
    assert raw["file_name"] == "1280X1280.PNG"
    # It must NOT surface the hidden internal-hint body.
    assert "internal hint" not in raw["text"]


def test_wrap_event_plain_text_unchanged():
    raw = MatrixTrigger()._wrap_event(
        event=_plain_text_event("hello"), room_id="!r:h", credential=_cred()
    )
    assert raw["kind"] == "m.room.message.text"
    assert raw["body"] == "hello"


# ── parse_event ─────────────────────────────────────────────────────────
def test_parse_event_compound_yields_text_and_attachment_ref():
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_text_event(), room_id="!wsorpuc:h", credential=_cred()
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    # real user text becomes the message content (not the internal hint)
    assert parsed.content == "what do you think of this advertisement picture?"
    assert parsed.content_type == MessageContentType.IMAGE
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    assert refs[0]["mxc_url"] == MXC
    assert refs[0]["mime_hint"] == "image/png"
    assert refs[0]["original_name"] == "1280X1280.PNG"


def test_parse_event_compound_text_only_no_media():
    """A compound_trigger with text but no media_url → plain text turn."""
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_text_event(text="hi there", media_url=""),
        room_id="!r:h", credential=_cred(),
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert parsed.content == "hi there"
    assert parsed.content_type == MessageContentType.TEXT
    assert "attachment_refs" not in parsed.raw


def test_parse_event_compound_caption_less_image():
    """Image with empty preview text still flows (refs carry it)."""
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_text_event(text=""),
        room_id="!r:h", credential=_cred(),
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert parsed.content == ""
    assert (parsed.raw.get("attachment_refs") or [])
