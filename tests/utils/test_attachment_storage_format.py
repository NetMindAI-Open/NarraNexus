"""
@file_name: test_attachment_storage_format.py
@author: Claude
@date: 2026-05-04
@description: Unit tests for format_attachments_for_system_prompt

Locks in the contract that the current-turn attachment block surfaces
the Whisper transcript inline for audio uploads. Without this, the
agent treats audio as opaque bytes and tells the user "I cannot listen
to audio".
"""

from __future__ import annotations

import pytest

from xyz_agent_context.utils import attachment_storage as at_storage


@pytest.fixture(autouse=True)
def _stub_resolve(monkeypatch):
    """resolve_attachment_path hits the workspace filesystem; stub it
    out so the formatter tests don't need real uploads on disk."""

    def _fake_resolve(agent_id, user_id, file_id):
        if not file_id:
            return None
        return f"/tmp/{agent_id}_{user_id}/{file_id}.bin"

    monkeypatch.setattr(at_storage, "resolve_attachment_path", _fake_resolve)


def test_format_returns_empty_when_no_attachments():
    out = at_storage.format_attachments_for_system_prompt(
        attachments=[], agent_id="ag", user_id="u"
    )
    assert out == ""


def test_format_includes_path_for_image():
    attachments = [
        {
            "file_id": "att_aaaa1111",
            "original_name": "cat.png",
            "mime_type": "image/png",
            "category": "image",
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    assert "name=cat.png" in out
    assert "path=/tmp/ag_u/att_aaaa1111.bin" in out
    # Only the heading-line mentions `transcript=...` as part of the
    # instructional copy. None of the per-attachment lines should
    # carry a transcript here.
    attachment_lines = [
        line for line in out.splitlines() if line.startswith("- name=")
    ]
    assert all("transcript=" not in line for line in attachment_lines)


def test_format_inlines_transcript_for_audio():
    attachments = [
        {
            "file_id": "att_bbbb2222",
            "original_name": "voice.mp3",
            "mime_type": "audio/mpeg",
            "category": "media",
            "transcript": "Hello world, this is a test.",
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    assert "transcript=Hello world, this is a test." in out
    assert "path=/tmp/ag_u/att_bbbb2222.bin" in out


def test_format_audio_blank_transcript_explains_why():
    """Audio with whitespace-only transcript is treated the same as
    missing — surface the unavailable hint so the agent can ask the
    user to configure an OpenAI provider."""
    attachments = [
        {
            "file_id": "att_cccc3333",
            "original_name": "voice.mp3",
            "mime_type": "audio/mpeg",
            "category": "media",
            "transcript": "   ",
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    attachment_lines = [
        line for line in out.splitlines() if line.startswith("- name=")
    ]
    assert "transcript=<unavailable" in attachment_lines[0]


def test_format_audio_missing_transcript_explains_why():
    attachments = [
        {
            "file_id": "att_dddd4444",
            "original_name": "voice.mp3",
            "mime_type": "audio/mpeg",
            "category": "media",
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    attachment_lines = [
        line for line in out.splitlines() if line.startswith("- name=")
    ]
    assert "transcript=<unavailable" in attachment_lines[0]
    assert "OpenAI" in attachment_lines[0]


def test_format_audio_non_string_transcript_explains_why():
    """Garbage in the transcript field is treated as missing."""
    attachments = [
        {
            "file_id": "att_eeee5555",
            "original_name": "voice.mp3",
            "mime_type": "audio/mpeg",
            "category": "media",
            "transcript": 12345,
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    attachment_lines = [
        line for line in out.splitlines() if line.startswith("- name=")
    ]
    assert "transcript=<unavailable" in attachment_lines[0]


def test_format_non_audio_missing_transcript_stays_silent():
    """Images / PDFs / text never carry a transcript — the unavailable
    hint must NOT appear for those, only for audio/* mimes."""
    attachments = [
        {
            "file_id": "att_dddd9999",
            "original_name": "doc.pdf",
            "mime_type": "application/pdf",
            "category": "document",
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    attachment_lines = [
        line for line in out.splitlines() if line.startswith("- name=")
    ]
    assert "transcript=" not in attachment_lines[0]


def test_format_strips_transcript_whitespace():
    attachments = [
        {
            "file_id": "att_ffff6666",
            "original_name": "voice.mp3",
            "mime_type": "audio/mpeg",
            "category": "media",
            "transcript": "  spoken content  \n",
        }
    ]
    out = at_storage.format_attachments_for_system_prompt(
        attachments=attachments, agent_id="ag", user_id="u"
    )
    assert "transcript=spoken content" in out
