"""
@file_name: test_attachments_sniff.py
@author: NarraNexus
@date: 2026-05-05
@description: Unit tests for the shared MIME sniffer (utils/mime_sniff).

Locks in the contract that audio-only WebM / Ogg / MP4 files (the shape
MediaRecorder produces from the in-browser AudioRecorder) get
classified as audio/<container> even when libmagic reports
video/<container>. Without this, browser-recorded voice clips skip
Whisper transcription entirely because the upload route's
``mime.startswith("audio/")`` gate fails.
"""
from __future__ import annotations

from xyz_agent_context.utils.mime_sniff import (
    _audio_video_container_override,
    sniff_mime_type,
)


# ---------------------------------------------------------------------------
# audio/* override fires when libmagic and browser agree on container
# ---------------------------------------------------------------------------


def test_overrides_video_webm_to_audio_webm_when_browser_says_audio():
    out = _audio_video_container_override("video/webm", "audio/webm")
    assert out == "audio/webm"


def test_overrides_video_webm_when_browser_includes_codecs_param():
    """MediaRecorder typically sends ``audio/webm;codecs=opus`` — the
    semicolon parameters must be stripped before container comparison."""
    out = _audio_video_container_override(
        "video/webm", "audio/webm;codecs=opus"
    )
    assert out == "audio/webm"


def test_overrides_video_ogg_to_audio_ogg():
    out = _audio_video_container_override("video/ogg", "audio/ogg")
    assert out == "audio/ogg"


def test_overrides_video_mp4_to_audio_mp4_for_safari_recording():
    """Safari's MediaRecorder records into MP4 with audio-only streams."""
    out = _audio_video_container_override("video/mp4", "audio/mp4")
    assert out == "audio/mp4"


def test_overrides_case_insensitive_browser_claim():
    out = _audio_video_container_override("video/webm", "Audio/WEBM")
    assert out == "audio/webm"


# ---------------------------------------------------------------------------
# Override does NOT fire — preserve libmagic's verdict
# ---------------------------------------------------------------------------


def test_no_override_when_browser_silent():
    """No content_type from the browser → trust libmagic as-is."""
    assert _audio_video_container_override("video/webm", None) == "video/webm"
    assert _audio_video_container_override("video/webm", "") == "video/webm"


def test_no_override_when_browser_claims_non_audio():
    """Browser says video/* or text/*; no audio claim, no override."""
    assert (
        _audio_video_container_override("video/webm", "video/webm")
        == "video/webm"
    )
    assert (
        _audio_video_container_override("video/webm", "text/plain")
        == "video/webm"
    )


def test_no_override_when_containers_disagree():
    """Browser says audio/mpeg but libmagic says video/webm — different
    containers, no tiebreaker possible. Don't fabricate a MIME the
    file doesn't claim by either method."""
    out = _audio_video_container_override("video/webm", "audio/mpeg")
    assert out == "video/webm"


def test_no_override_when_libmagic_already_audio():
    """libmagic reported audio/* directly — no override path needed."""
    out = _audio_video_container_override("audio/mpeg", "audio/webm")
    assert out == "audio/mpeg"


def test_no_override_for_non_video_libmagic_results():
    """image/* or application/* — passthrough."""
    assert (
        _audio_video_container_override("image/png", "audio/webm")
        == "image/png"
    )
    assert (
        _audio_video_container_override("application/pdf", "audio/webm")
        == "application/pdf"
    )


# ---------------------------------------------------------------------------
# sniff_mime_type end-to-end — override must apply on EVERY tier
#
# Regression: without applying the override to the mimetypes.guess_type
# fallback, environments without python-magic fall through to the stdlib
# which hardcodes ``video/webm`` for `.webm`, defeating the override
# entirely. The in-browser AudioRecorder hits exactly this path on dev
# machines where libmagic isn't installed.
# ---------------------------------------------------------------------------


def test_sniff_mimetypes_fallback_overrides_video_webm_to_audio():
    """When libmagic is absent and the stdlib guesses ``video/webm``
    by extension, the browser's ``audio/webm`` claim must still win
    via the same override."""
    out = sniff_mime_type(b"", filename="voice_1234.webm", client_type="audio/webm;codecs=opus")
    # If python-magic is installed and sniffs ``video/webm`` from empty
    # bytes, the override fires; if absent, mimetypes returns
    # ``video/webm`` from the .webm extension and the override fires
    # there. Either way, the result must be ``audio/webm``.
    assert out == "audio/webm"


def test_sniff_mimetypes_fallback_overrides_video_mp4_to_audio_for_safari():
    out = sniff_mime_type(b"", filename="voice_5678.mp4", client_type="audio/mp4")
    assert out == "audio/mp4"


def test_sniff_mimetypes_fallback_keeps_video_when_browser_silent():
    """No browser-supplied audio claim → no override → stays as the
    stdlib's verdict. (PDFs, real videos, etc. shouldn't be silently
    rewritten.)"""
    out = sniff_mime_type(b"", filename="clip.webm", client_type=None)
    assert out == "video/webm"


def test_sniff_unknown_extension_falls_back_to_browser_content_type():
    """Last-resort path: extension unknown to mimetypes, no libmagic
    sniff, fall through to the raw browser claim."""
    out = sniff_mime_type(b"", filename="blob.xyz123", client_type="audio/webm")
    assert out == "audio/webm"


def test_sniff_unknown_extension_no_browser_returns_octet_stream():
    out = sniff_mime_type(b"", filename="blob.xyz123", client_type=None)
    assert out == "application/octet-stream"
