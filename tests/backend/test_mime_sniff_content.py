"""
@file_name: test_mime_sniff_content.py
@author:
@date: 2026-08-12
@description: Pin that MIME sniffing uses real content, not the extension
(Mark's item 8).

`sniff_mime_type` advertised a libmagic content tier, but python-magic was
never a declared dependency, so the ImportError branch silently swallowed it
and the sniffer fell back to the (spoofable) extension / client Content-Type.
A WAV file renamed `.png` and declared `image/png` was classified as an
image. With python-magic declared and libmagic present, content wins: real
WAV bytes classify as audio regardless of the lying name/type.
"""
from __future__ import annotations

import struct

import pytest

# This test asserts real content-based sniffing, which needs the native
# libmagic. python-magic raises ImportError at import when the native lib is
# absent (macOS desktop, the cloud python:3.13-slim image before its companion
# deploy change) — skip there rather than fail on a host-library gap.
pytest.importorskip("magic", reason="requires system libmagic")

from narranexus.platform.utils.mime_sniff import sniff_mime_type  # noqa: E402


def _wav_bytes() -> bytes:
    """A minimal but valid PCM WAV header libmagic recognises as audio."""
    sample_data = b"\x00\x00" * 8
    fmt_chunk = struct.pack("<HHIIHH", 1, 1, 8000, 16000, 2, 16)
    data_chunk = struct.pack("<4sI", b"data", len(sample_data)) + sample_data
    fmt = struct.pack("<4sI", b"fmt ", len(fmt_chunk)) + fmt_chunk
    body = b"WAVE" + fmt + data_chunk
    return struct.pack("<4sI", b"RIFF", len(body)) + body


def test_content_beats_spoofed_extension_and_type():
    mime = sniff_mime_type(_wav_bytes(), filename="voice.png", client_type="image/png")
    assert mime.startswith("audio/"), f"expected audio/*, got {mime}"
