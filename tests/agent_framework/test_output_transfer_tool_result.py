"""
@file_name: test_output_transfer_tool_result.py
@description: Regression tests for _stringify_tool_result_content.

A ToolResultBlock.content arrives from the Claude Agent SDK as either a bare
string or a list of content blocks. MCP tools that return a dict (e.g.
create_artifact) come back as a single text block whose `text` is the
JSON-encoded result. The old `str(block.content)` produced a Python repr for
the list case — NOT valid JSON — which silently broke every frontend consumer
that JSON.parses tool_output. These tests pin the flattening behaviour.
"""

from __future__ import annotations

import json

from narranexus.platform.agent_framework.loop.output_transfer import (
    _stringify_tool_result_content,
)


class _FakeTextBlock:
    """Mimics an SDK block object exposing a `.text` attribute."""

    def __init__(self, text: str) -> None:
        self.text = text


def test_bare_string_passthrough() -> None:
    assert _stringify_tool_result_content("hello") == "hello"


def test_none_returns_empty_string() -> None:
    assert _stringify_tool_result_content(None) == ""


def test_list_of_text_block_dicts_yields_clean_json() -> None:
    payload = json.dumps({"artifact_id": "art_abcd1234", "version": 1})
    content = [{"type": "text", "text": payload}]
    out = _stringify_tool_result_content(content)
    # Must be parseable JSON with the artifact_id at the top level — this is
    # exactly what the frontend's ArtifactToolCallCards relies on.
    assert json.loads(out)["artifact_id"] == "art_abcd1234"


def test_list_of_sdk_block_objects() -> None:
    payload = json.dumps({"artifact_id": "art_ffff0000"})
    content = [_FakeTextBlock(payload)]
    out = _stringify_tool_result_content(content)
    assert json.loads(out)["artifact_id"] == "art_ffff0000"


def test_list_of_plain_strings_concatenates() -> None:
    assert _stringify_tool_result_content(["foo", "bar"]) == "foobar"


def test_dict_without_text_key_falls_back_to_json() -> None:
    # A non-text, non-image block shouldn't crash — it round-trips through
    # json.dumps so the result stays machine-readable.
    content = [{"type": "resource_link", "uri": "file:///tmp/report.csv"}]
    out = _stringify_tool_result_content(content)
    assert json.loads(out)["uri"] == "file:///tmp/report.csv"


def test_sdk_image_block_keeps_a_descriptor_but_never_its_base64() -> None:
    # The model already received the image natively. The persisted tool
    # output feeds the UI, the database and later-turn history replay, so
    # megabytes of base64 there would be pure context and storage bloat.
    data = "iVBORw0KGgo" + "A" * 300_000
    content = [
        {"type": "text", "text": '{"observation_id": "view_1"}'},
        {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
    ]
    out = _stringify_tool_result_content(content)
    assert data not in out
    assert len(out) < 500
    assert out.startswith('{"observation_id": "view_1"}')
    assert "image/png" in out


def test_mcp_shaped_image_block_is_described_not_inlined() -> None:
    data = "R0lGOD" + "B" * 50_000
    out = _stringify_tool_result_content([{"type": "image", "mimeType": "image/jpeg", "data": data}])
    assert data not in out
    described = json.loads(out)
    assert described == {"type": "image", "mime_type": "image/jpeg", "base64_chars": len(data)}


def test_multi_block_list_is_joined_in_order() -> None:
    content = [
        {"type": "text", "text": "part-1;"},
        {"type": "text", "text": "part-2"},
    ]
    assert _stringify_tool_result_content(content) == "part-1;part-2"
