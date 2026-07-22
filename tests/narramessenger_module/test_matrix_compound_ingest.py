"""
@file_name: test_matrix_compound_ingest.py
@date: 2026-07-20
@description: NarraMessenger "compound message" ingest for MatrixTrigger.

NarraMessenger does NOT deliver document/image attachments as standard
inline m.image / m.file events. A picture/file arrives as a SINGLE event
with the custom msgtype ``ai.netmind.compound``, whose
``content["ai.netmind.compound"]`` block self-describes the media
(``media_url`` / ``mime_type`` / ``file_name`` / ``size``) plus the REAL
user text; the @-mention rides in ``content["m.mentions"]`` on the same
event. Because the msgtype is custom, nio has no factory for it and parses
the event as ``RoomMessageUnknown`` — so ingest keys off the raw ``source``
content, NOT the nio class (an isinstance check misses it → the attachment
is silently dropped and the agent never sees it). Verified on the wire
2026-07-20 (agent_743423ca551b): PDF ``application/pdf`` + image
``image/jpeg`` both arrive this way; only ``mime_type`` differs.

(History: until 2026-07-03 the same payload arrived as a hidden ``m.text``
+ ``content["ai.netmind.hint"].compound_trigger`` preview alongside an
ignored ``ai.netmind.compound`` sibling. That two-event hint shape is gone
— per 铁律 #2 the old path is removed, not kept as a compat shim.)

Standard inline media (voice notes arrive as ``m.audio``, etc.) is a
DIFFERENT path — nio parses those as RoomMessageMedia and the media branch
handles them unchanged; they are covered elsewhere.

Locks:
  - _wrap_event lifts an ``ai.netmind.compound`` event into a compound raw
    dict from ``content["ai.netmind.compound"]`` (text + media_url + mime +
    filename + size), regardless of the concrete mime.
  - parse_event turns it into a ParsedMessage whose content is the real
    user text and whose raw["attachment_refs"] points at the mxc, so the
    existing fetch_attachments pipeline downloads it. content_type follows
    the mime (image → IMAGE, pdf → FILE).
  - _is_mentioning_us reads m.mentions off the wrapped event's raw source,
    so a compound @-mention classifies as group_mention (not group_silent).
  - a plain text event (no compound block) is unchanged.
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


def _compound_event(
    *,
    text: str = "what do you think of this document?",
    media_url: str = MXC,
    mime: str = "application/pdf",
    file_name: str = "report.pdf",
    size: int = 718537,
    mention: bool = True,
    event_id: str = "$iMJxdQLYGp59E9ZrYFNoTqwH_Pp_PoXh8h32tb5LdYg",
) -> Any:
    """A real nio-parsed event carrying NarraMessenger's single-event
    ``ai.netmind.compound`` payload — exactly as observed on the wire
    (2026-07-20). Parsed through nio so it lands as RoomMessageUnknown,
    the same class the live sync loop hands to _wrap_event."""
    from nio.events.room_events import RoomMessage

    payload: dict[str, Any] = {}
    if text:
        payload["text"] = text
    if media_url:
        payload["media_url"] = media_url
    if mime:
        payload["mime_type"] = mime
    if file_name:
        payload["file_name"] = file_name
    if size:
        payload["size"] = size
    content: dict[str, Any] = {
        "msgtype": "ai.netmind.compound",
        "body": f"[ai.netmind.compound] {file_name}",
        "ai.netmind.compound": payload,
    }
    if mention:
        content["m.mentions"] = {"user_ids": [AGENT_MXID]}
    ev = RoomMessage.parse_event(
        {
            "type": "m.room.message",
            "event_id": event_id,
            "sender": SENDER,
            "origin_server_ts": 1784579546122,
            "content": content,
        }
    )
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


# ── nio really does classify the custom msgtype as Unknown ──────────────
def test_nio_parses_compound_msgtype_as_unknown():
    # Regression guard for the whole fix: if nio ever grows a factory for
    # ai.netmind.compound this assumption (and the wrap branch) must be
    # revisited. Today it is RoomMessageUnknown → isinstance(Text/Media)
    # both False → the branch MUST key off raw content, not the class.
    from nio import RoomMessageMedia, RoomMessageText

    ev = _compound_event()
    assert not isinstance(ev, RoomMessageText)
    assert not isinstance(ev, RoomMessageMedia)
    assert type(ev).__name__ == "RoomMessageUnknown"


# ── _wrap_event ─────────────────────────────────────────────────────────
def test_wrap_event_lifts_compound_document():
    raw = MatrixTrigger()._wrap_event(
        event=_compound_event(), room_id="!wsorpuc:h", credential=_cred()
    )
    assert raw is not None
    assert raw["kind"] == "m.room.message.compound"
    assert raw["text"] == "what do you think of this document?"
    assert raw["mxc_url"] == MXC
    assert raw["mimetype"] == "application/pdf"
    assert raw["file_name"] == "report.pdf"
    assert raw["size"] == 718537
    # The wrapped event must carry the raw nio object so downstream mention
    # detection can read m.mentions off its .source.
    assert raw["_nio_event"] is not None


def test_wrap_event_lifts_compound_image_same_shape():
    # An image is the SAME compound event — only the mime differs. One
    # branch must cover every attachment mime.
    raw = MatrixTrigger()._wrap_event(
        event=_compound_event(
            text="这个图呢？",
            mime="image/jpeg",
            file_name="20260625-101622.jpeg",
            size=202280,
        ),
        room_id="!r:h",
        credential=_cred(),
    )
    assert raw["kind"] == "m.room.message.compound"
    assert raw["mimetype"] == "image/jpeg"
    assert raw["file_name"] == "20260625-101622.jpeg"
    assert raw["text"] == "这个图呢？"


def test_wrap_event_plain_text_unchanged():
    raw = MatrixTrigger()._wrap_event(
        event=_plain_text_event("hello"), room_id="!r:h", credential=_cred()
    )
    assert raw["kind"] == "m.room.message.text"
    assert raw["body"] == "hello"


# ── mention detection (the actual "no reply" symptom) ───────────────────
def test_compound_mention_is_detected():
    # The bug's user-visible symptom: an @-mention riding on a compound
    # event was dropped, so the group message never became group_mention.
    # Once wrapped, _is_mentioning_us must see the m.mentions on .source.
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_event(), room_id="!r:h", credential=_cred()
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert t._is_mentioning_us(parsed, _cred()) is True


def test_compound_without_mention_not_detected():
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_event(mention=False), room_id="!r:h", credential=_cred()
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert t._is_mentioning_us(parsed, _cred()) is False


# ── parse_event ─────────────────────────────────────────────────────────
def test_parse_event_compound_yields_text_and_attachment_ref():
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_event(
            text="这个图呢？",
            mime="image/jpeg",
            file_name="20260625-101622.jpeg",
        ),
        room_id="!wsorpuc:h",
        credential=_cred(),
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    # real user text becomes the message content
    assert parsed.content == "这个图呢？"
    assert parsed.content_type == MessageContentType.IMAGE
    refs = parsed.raw.get("attachment_refs") or []
    assert len(refs) == 1
    assert refs[0]["mxc_url"] == MXC
    assert refs[0]["mime_hint"] == "image/jpeg"
    assert refs[0]["original_name"] == "20260625-101622.jpeg"


def test_parse_event_compound_document_content_type_is_file():
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_event(mime="application/pdf", file_name="report.pdf"),
        room_id="!r:h",
        credential=_cred(),
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert parsed.content_type == MessageContentType.FILE
    assert (parsed.raw.get("attachment_refs") or [])[0]["mime_hint"] == "application/pdf"


def test_parse_event_compound_text_only_no_media():
    """A compound with text but no media_url → plain text turn."""
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_event(text="hi there", media_url=""),
        room_id="!r:h", credential=_cred(),
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert parsed.content == "hi there"
    assert parsed.content_type == MessageContentType.TEXT
    assert "attachment_refs" not in parsed.raw


def test_parse_event_compound_caption_less_image():
    """Image with empty text still flows (refs carry it)."""
    t = MatrixTrigger()
    raw = t._wrap_event(
        event=_compound_event(text="", mime="image/jpeg"),
        room_id="!r:h", credential=_cred(),
    )
    parsed = t.parse_event(raw)
    assert parsed is not None
    assert parsed.content == ""
    assert (parsed.raw.get("attachment_refs") or [])
