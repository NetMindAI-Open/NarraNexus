"""
@file_name: test_download.py
@author:
@date: 2026-09-22
@description: Tests for the browser runtime downloader — URL resolution and
the resumable fetch loop.

Two things here are load-bearing for real users rather than for correctness in
the abstract:

* **Mirror host override.** Local-mode users in mainland China are the main
  population, not an edge case. If the mirror cannot be pointed
  somewhere reachable, the feature simply does not install for them.
* **Resume.** A 150 MB download over a flaky link that restarts from zero
  every time never finishes. The range request and the partial-file
  bookkeeping are therefore tested, not assumed.

No network: the fetch loop takes an injected async chunk source.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.platform.browser._browser_impl.download import (
    DownloadSpec,
    fetch_resumable,
    resolve_download_url,
)


# ── URL resolution / mirror override ─────────────────────────────────────────


def test_default_host_used_when_no_override():
    url = resolve_download_url(spec=DownloadSpec(revision="1234", archive="chromium-mac-arm64.zip"))
    assert url.startswith("https://")
    assert "1234" in url
    assert url.endswith("chromium-mac-arm64.zip")


def test_mirror_override_replaces_host_and_keeps_path():
    url = resolve_download_url(
        spec=DownloadSpec(revision="1234", archive="chromium-mac-arm64.zip"),
        host="https://mirror.example.cn/playwright",
    )
    assert url.startswith("https://mirror.example.cn/playwright/")
    assert "1234" in url
    assert url.endswith("chromium-mac-arm64.zip")


def test_mirror_override_tolerates_trailing_slash():
    a = resolve_download_url(
        spec=DownloadSpec(revision="1", archive="x.zip"), host="https://m.example.cn/base/"
    )
    b = resolve_download_url(
        spec=DownloadSpec(revision="1", archive="x.zip"), host="https://m.example.cn/base"
    )
    assert a == b
    assert "//x.zip" not in a.replace("https://", "")


def test_blank_override_falls_back_to_default():
    """An env var set to empty string is 'unset', not 'download from nowhere'."""
    default = resolve_download_url(spec=DownloadSpec(revision="1", archive="x.zip"))
    for blank in ("", "   "):
        assert resolve_download_url(spec=DownloadSpec(revision="1", archive="x.zip"), host=blank) == default


def test_non_https_override_is_rejected():
    """A plain-http mirror would let anyone on the path swap the browser we
    are about to execute. Refuse rather than silently downgrade."""
    with pytest.raises(ValueError, match="https"):
        resolve_download_url(
            spec=DownloadSpec(revision="1", archive="x.zip"), host="http://mirror.example.cn"
        )


# ── resumable fetch ──────────────────────────────────────────────────────────


class FakeSource:
    """Yields chunks; records the byte offset it was asked to start from."""

    def __init__(self, payload: bytes, *, fail_after: int | None = None, total: int | None = None):
        self.payload = payload
        self.fail_after = fail_after
        self.total = total if total is not None else len(payload)
        self.requested_offsets: list[int] = []

    async def __call__(self, *, offset: int):
        self.requested_offsets.append(offset)
        sent = 0
        for i in range(offset, len(self.payload), 4):
            if self.fail_after is not None and sent >= self.fail_after:
                raise ConnectionError("link dropped")
            chunk = self.payload[i : i + 4]
            sent += len(chunk)
            yield chunk, self.total


@pytest.mark.asyncio
async def test_fetch_writes_the_whole_payload(tmp_path: Path):
    dest = tmp_path / "chromium.zip"
    src = FakeSource(b"0123456789abcdef")

    got = await fetch_resumable(dest=dest, source=src, on_progress=lambda _p: None,
                                cancelled=lambda: False)

    assert got == len(b"0123456789abcdef")
    assert dest.read_bytes() == b"0123456789abcdef"
    assert src.requested_offsets == [0]


@pytest.mark.asyncio
async def test_fetch_resumes_from_the_partial_file(tmp_path: Path):
    dest = tmp_path / "chromium.zip"
    dest.write_bytes(b"01234567")  # 8 bytes already on disk
    src = FakeSource(b"0123456789abcdef")

    await fetch_resumable(dest=dest, source=src, on_progress=lambda _p: None,
                          cancelled=lambda: False)

    assert src.requested_offsets == [8], "must range-request, not restart"
    assert dest.read_bytes() == b"0123456789abcdef"


@pytest.mark.asyncio
async def test_interrupted_fetch_leaves_a_resumable_partial(tmp_path: Path):
    dest = tmp_path / "chromium.zip"
    src = FakeSource(b"0123456789abcdef", fail_after=8)

    with pytest.raises(ConnectionError):
        await fetch_resumable(dest=dest, source=src, on_progress=lambda _p: None,
                              cancelled=lambda: False)

    # Whatever arrived must be on disk, or the next attempt gains nothing.
    assert dest.exists()
    assert dest.read_bytes() == b"01234567"


@pytest.mark.asyncio
async def test_cancel_stops_mid_stream_and_keeps_the_partial(tmp_path: Path):
    dest = tmp_path / "chromium.zip"
    src = FakeSource(b"0123456789abcdef")
    seen: list[int] = []

    def cancelled() -> bool:
        # Cancel once some bytes have landed.
        return dest.exists() and dest.stat().st_size >= 8

    await fetch_resumable(dest=dest, source=src, on_progress=lambda p: seen.append(p.bytes_done),
                          cancelled=cancelled)

    assert dest.stat().st_size >= 8
    assert dest.stat().st_size < len(b"0123456789abcdef")


@pytest.mark.asyncio
async def test_progress_counts_bytes_already_on_disk(tmp_path: Path):
    """Resuming at 8/16 must report ~50%, not 0% — a progress bar that
    restarts at zero on resume reads as 'it lost my download'."""
    dest = tmp_path / "chromium.zip"
    dest.write_bytes(b"01234567")
    src = FakeSource(b"0123456789abcdef")
    samples = []

    await fetch_resumable(dest=dest, source=src, on_progress=samples.append,
                          cancelled=lambda: False)

    assert samples, "must report at least one sample"
    assert samples[0].bytes_done > 8
    assert samples[0].bytes_total == 16
    assert samples[-1].bytes_done == 16
    assert all(s.phase == "downloading" for s in samples)


@pytest.mark.asyncio
async def test_progress_is_monotonic(tmp_path: Path):
    dest = tmp_path / "chromium.zip"
    src = FakeSource(bytes(64))
    samples = []

    await fetch_resumable(dest=dest, source=src, on_progress=samples.append,
                          cancelled=lambda: False)

    assert [s.bytes_done for s in samples] == sorted(s.bytes_done for s in samples)


@pytest.mark.asyncio
async def test_creates_missing_parent_directory(tmp_path: Path):
    dest = tmp_path / "nested" / "deeper" / "chromium.zip"
    src = FakeSource(b"abcd")

    await fetch_resumable(dest=dest, source=src, on_progress=lambda _p: None,
                          cancelled=lambda: False)

    assert dest.read_bytes() == b"abcd"


@pytest.mark.asyncio
async def test_cancel_before_fetch_does_not_open_the_transport(tmp_path):
    source = FakeSource(b"abcd")
    await fetch_resumable(dest=tmp_path / "partial", source=source,
                          on_progress=lambda _: None, cancelled=lambda: True)
    assert source.requested_offsets == []


@pytest.mark.asyncio
async def test_short_response_keeps_partial_but_fails(tmp_path):
    dest = tmp_path / "partial"
    with pytest.raises(OSError, match="incomplete"):
        await fetch_resumable(dest=dest, source=FakeSource(b"abcd", total=8),
                              on_progress=lambda _: None, cancelled=lambda: False)
    assert dest.read_bytes() == b"abcd"


@pytest.mark.asyncio
async def test_cancel_interrupts_a_stalled_transport(tmp_path):
    import asyncio
    started, closed = asyncio.Event(), asyncio.Event()
    stop = False

    async def source(*, offset):
        try:
            started.set()
            await asyncio.Event().wait()
            yield b"unused", None
        finally:
            closed.set()

    task = asyncio.create_task(fetch_resumable(
        dest=tmp_path / "partial", source=source, on_progress=lambda _: None, cancelled=lambda: stop))
    await started.wait()
    stop = True
    assert await asyncio.wait_for(task, 2) == 0
    assert closed.is_set()
