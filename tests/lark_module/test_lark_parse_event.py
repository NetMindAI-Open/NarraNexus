"""
@file_name: test_lark_parse_event.py
@date: 2026-05-21
@description: Regression tests for ``LarkTrigger.parse_event``.

Phase 1c bundles a fix for the pre-existing JSON-fallback bug where
non-text messages (file / image / audio / media / sticker / unknown)
had their raw JSON content string leaked into ``ParsedMessage.content``.
Symptom: the agent's prompt received literal
``{"file_key":"file_v3_xxx","file_name":"report.pdf"}`` instead of the
empty string + an ``attachment_refs`` ref. This file pins the fixed
behavior with one regression test per message_type.

Tests are pure (no DB / no async); ``parse_event`` is a sync method
operating on a raw dict.
"""
from __future__ import annotations

import json

from xyz_agent_context.module.lark_module.lark_trigger import LarkTrigger


def _make_trigger() -> LarkTrigger:
    """Cheap trigger instance — parse_event is pure, no lifecycle needed."""
    return LarkTrigger()


def _raw(
    *,
    message_type: str,
    content_payload: dict | str | None,
    msg_id: str = "om_msg_1",
    sender_id: str = "ou_sender",
) -> dict:
    """Build a minimal Lark event dict.

    ``content_payload`` is the *Python* value; the function JSON-encodes
    dicts (Lark's wire format is a JSON-encoded string in ``content``).
    Pass a string verbatim to test plain-text content; pass None to
    omit the field.
    """
    raw: dict = {
        "message_id": msg_id,
        "chat_id": "oc_chat",
        "sender_id": sender_id,
        "sender_name": "Tong",
        "message_type": message_type,
        "create_time": "1779349247000",
    }
    if isinstance(content_payload, dict):
        raw["content"] = json.dumps(content_payload, ensure_ascii=False)
    elif isinstance(content_payload, str):
        raw["content"] = content_payload
    return raw


# ─────────────────────────────────────────────────────────────────────
# Text messages — must still extract content as before (regression
# guarantee that the bug fix didn't break the happy path).
# ─────────────────────────────────────────────────────────────────────

def test_text_message_extracts_text_field() -> None:
    parsed = _make_trigger().parse_event(
        _raw(message_type="text", content_payload={"text": "hello world"})
    )
    assert parsed is not None
    assert parsed.content == "hello world"


def test_text_message_with_unicode_extracts_correctly() -> None:
    parsed = _make_trigger().parse_event(
        _raw(message_type="text", content_payload={"text": "你好 🦊 world"})
    )
    assert parsed is not None
    assert parsed.content == "你好 🦊 world"


def test_text_message_empty_text_field_produces_empty_content() -> None:
    parsed = _make_trigger().parse_event(
        _raw(message_type="text", content_payload={"text": ""})
    )
    assert parsed is not None
    assert parsed.content == ""


# ─────────────────────────────────────────────────────────────────────
# File / image / audio / media messages — the JSON-fallback bug pin.
# Before fix: parsed.content would be the raw JSON string.
# After fix: parsed.content is empty string (file metadata flows via
# attachment_refs in Phase 1c T9b/c/d, NOT via content).
# ─────────────────────────────────────────────────────────────────────

def test_file_message_does_not_leak_json_into_content() -> None:
    parsed = _make_trigger().parse_event(
        _raw(
            message_type="file",
            content_payload={
                "file_key": "file_v3_xxxxxxxxxxxxxxxx",
                "file_name": "report.pdf",
                "file_size": 154823,
            },
        )
    )
    assert parsed is not None
    assert parsed.content == "", (
        "file message MUST NOT leak JSON metadata into content "
        f"(got: {parsed.content!r})"
    )


def test_image_message_does_not_leak_json_into_content() -> None:
    parsed = _make_trigger().parse_event(
        _raw(
            message_type="image",
            content_payload={"image_key": "img_v3_xxxxxxxxxxxxxxxx"},
        )
    )
    assert parsed is not None
    assert parsed.content == ""


def test_audio_message_does_not_leak_json_into_content() -> None:
    parsed = _make_trigger().parse_event(
        _raw(
            message_type="audio",
            content_payload={
                "file_key": "audio_xxxxxxxxxxxxxxxx",
                "duration": 5400,
            },
        )
    )
    assert parsed is not None
    assert parsed.content == ""


def test_media_message_does_not_leak_json_into_content() -> None:
    """``media`` is Lark's video-with-audio bucket."""
    parsed = _make_trigger().parse_event(
        _raw(
            message_type="media",
            content_payload={
                "file_key": "media_xxxxxxxxxxxxxxxx",
                "file_name": "demo.mp4",
                "duration": 60000,
            },
        )
    )
    assert parsed is not None
    assert parsed.content == ""


def test_sticker_message_does_not_leak_json_into_content() -> None:
    parsed = _make_trigger().parse_event(
        _raw(
            message_type="sticker",
            content_payload={"file_key": "stk_xxxxxxxxxxxxxxxx"},
        )
    )
    assert parsed is not None
    assert parsed.content == ""


def test_sticker_with_text_field_in_payload_still_produces_empty_content() -> None:
    """Regression for HIGH-3: sticker payload may carry a ``text`` field
    (sticker description, platform display metadata) but that text is NOT
    user-typed and MUST NOT enter the agent prompt.

    Prior implementation checked ``"text" in payload`` BEFORE the
    message_type branch — a sticker payload with ``{"file_key":"stk_x",
    "text":"smile"}`` would leak "smile" as if the user had typed it.
    Fix: hard-gate on ``_NO_USER_TEXT_MESSAGE_TYPES`` first.
    """
    for media_type in ("image", "file", "audio", "media", "sticker"):
        parsed = _make_trigger().parse_event(
            _raw(
                message_type=media_type,
                content_payload={
                    "file_key": f"{media_type}_xxx",
                    "text": "INJECTED — should never reach the agent",
                },
            )
        )
        assert parsed is not None, f"{media_type} parse should succeed"
        assert parsed.content == "", (
            f"{media_type} with payload-side 'text' field leaked into content: "
            f"{parsed.content!r}"
        )


# ─────────────────────────────────────────────────────────────────────
# Post messages — rich-text multi-segment payloads. Must extract
# title + segment texts, not leak the entire nested JSON.
# ─────────────────────────────────────────────────────────────────────

def test_post_message_extracts_title_and_body_text() -> None:
    payload = {
        "zh_cn": {
            "title": "项目周报",
            "content": [
                [
                    {"tag": "text", "text": "本周完成了 "},
                    {"tag": "a", "text": "三件大事", "href": "https://example.com"},
                ],
                [
                    {"tag": "text", "text": "下周计划继续优化"},
                ],
            ],
        }
    }
    parsed = _make_trigger().parse_event(
        _raw(message_type="post", content_payload=payload)
    )
    assert parsed is not None
    # Title + body segments concatenated (order: title first, then segment text bits).
    assert "项目周报" in parsed.content
    assert "三件大事" in parsed.content
    assert "下周计划继续优化" in parsed.content


def test_post_message_with_multiple_language_blocks_picks_first_non_empty() -> None:
    """The post walker iterates language blocks and stops at the first
    one with non-empty content. To verify "first non-empty wins" (not
    "all blocks concatenated"), put DIFFERENT content in each language
    block and assert only the winning block's content appears, NOT a
    concatenation of both. ``en_us`` is empty so ``zh_cn`` wins.
    """
    payload = {
        "en_us": {"title": "", "content": []},
        "zh_cn": {
            "title": "中文标题",
            "content": [[{"tag": "text", "text": "中文正文"}]],
        },
        "ja_jp": {  # extra language with DIFFERENT content
            "title": "日本語タイトル",
            "content": [[{"tag": "text", "text": "日本語本文"}]],
        },
    }
    parsed = _make_trigger().parse_event(
        _raw(message_type="post", content_payload=payload)
    )
    assert parsed is not None
    # zh_cn won (first non-empty after en_us skipped).
    assert "中文标题" in parsed.content
    assert "中文正文" in parsed.content
    # Negative assertions: ja_jp's content MUST NOT leak — proves
    # the walker didn't concatenate all language blocks.
    assert "日本語タイトル" not in parsed.content
    assert "日本語本文" not in parsed.content


def test_post_message_empty_payload_produces_empty_content() -> None:
    parsed = _make_trigger().parse_event(
        _raw(message_type="post", content_payload={})
    )
    assert parsed is not None
    assert parsed.content == ""


# ─────────────────────────────────────────────────────────────────────
# Edge cases that exercised the OLD `text = content_str` fallback
# ─────────────────────────────────────────────────────────────────────

def test_unknown_message_type_with_text_field_still_extracts() -> None:
    """If a future Lark message_type still carries a top-level "text"
    field, surface it. This is a forward-compat insurance — Lark adds
    new types over time."""
    parsed = _make_trigger().parse_event(
        _raw(message_type="ephemeral", content_payload={"text": "future-feature"})
    )
    assert parsed is not None
    assert parsed.content == "future-feature"


def test_unknown_message_type_without_text_field_is_empty() -> None:
    parsed = _make_trigger().parse_event(
        _raw(message_type="some-new-type", content_payload={"some_field": "data"})
    )
    assert parsed is not None
    assert parsed.content == ""


def test_plain_string_content_without_json_envelope_preserved() -> None:
    """Legacy test fixtures pass raw strings as content. Don't break those."""
    parsed = _make_trigger().parse_event(
        _raw(message_type="text", content_payload="hi raw")
    )
    assert parsed is not None
    assert parsed.content == "hi raw"


def test_malformed_json_content_does_not_crash() -> None:
    """``content`` starts with '{' but is not valid JSON. Old code's
    try/except swallowed; new code should also not raise."""
    parsed = _make_trigger().parse_event(
        _raw(message_type="text", content_payload="{not-real-json")
    )
    assert parsed is not None
    # Empty fallback is fine; the important assertion is no crash.
    assert isinstance(parsed.content, str)


def test_missing_message_type_does_not_crash() -> None:
    """Legacy events without ``message_type`` field. Should not raise."""
    raw = {
        "message_id": "om_x",
        "chat_id": "oc_x",
        "sender_id": "ou_x",
        "sender_name": "Tong",
        "content": json.dumps({"text": "legacy"}),
        "create_time": "1779349247000",
    }
    parsed = _make_trigger().parse_event(raw)
    assert parsed is not None
    # Falls back via "text" key presence in payload.
    assert parsed.content == "legacy"


def test_missing_content_field_does_not_crash() -> None:
    raw = {
        "message_id": "om_x",
        "chat_id": "oc_x",
        "sender_id": "ou_x",
        "sender_name": "Tong",
        "message_type": "text",
        "create_time": "1779349247000",
    }
    parsed = _make_trigger().parse_event(raw)
    assert parsed is not None
    assert parsed.content == ""


# ─────────────────────────────────────────────────────────────────────
# Pass-through verification — raw dict + IDs + timestamp preserved.
# ─────────────────────────────────────────────────────────────────────

def test_raw_dict_original_fields_preserved_on_parsed() -> None:
    raw = _raw(message_type="file", content_payload={"file_key": "fk", "file_name": "x.pdf"})
    parsed = _make_trigger().parse_event(raw)
    assert parsed is not None
    # ParsedMessage.raw must carry every original Lark field so is_echo and
    # downstream consumers can read Lark-specific data (sender_type, etc.).
    # Phase 1c may ADD ``attachment_refs`` for media-type messages — the
    # parser shallow-copies before adding so the caller's dict is untouched,
    # but parsed.raw is allowed to be a superset of the input.
    for k, v in raw.items():
        assert parsed.raw.get(k) == v, f"missing/changed field: {k}"
    assert parsed.raw["message_type"] == "file"


def test_raw_dict_input_not_mutated_when_refs_added() -> None:
    """parse_event must not mutate the caller's raw dict."""
    raw = _raw(message_type="file", content_payload={"file_key": "fk", "file_name": "x.pdf"})
    snapshot = dict(raw)
    parsed = _make_trigger().parse_event(raw)
    assert parsed is not None
    # Caller's raw dict must NOT gain attachment_refs (parser uses a copy).
    assert "attachment_refs" not in raw
    assert raw == snapshot


def test_create_time_parsed_to_milliseconds() -> None:
    raw = _raw(message_type="text", content_payload={"text": "hi"})
    raw["create_time"] = "1779349247000"
    parsed = _make_trigger().parse_event(raw)
    assert parsed is not None
    assert parsed.timestamp_ms == 1779349247000


def test_create_time_non_numeric_falls_back_to_zero() -> None:
    raw = _raw(message_type="text", content_payload={"text": "hi"})
    raw["create_time"] = "not-a-number"
    parsed = _make_trigger().parse_event(raw)
    assert parsed is not None
    assert parsed.timestamp_ms == 0
