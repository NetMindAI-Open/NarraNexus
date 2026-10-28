"""
@file_name: test_downloader.py
@author:
@date: 2026-09-22
@description: Tests for the real downloader — build resolution, extraction, install.

The source is Google's Chrome for Testing index, which publishes a JSON
manifest of download URLs per platform. That is preferred over guessing a
vendor revision number because the manifest is the vendor's own statement of
"this build exists at this URL", so a bad version fails at resolution with a
clear message instead of as a 404 halfway through 150 MB.

Two risks get most of the attention here:

* **Zip slip.** We unpack an archive fetched over the network into the user's
  home directory. A member named `../../.ssh/authorized_keys` must be refused,
  not written.
* **Half-installs.** A download that dies mid-extract must not leave something
  that `locate_executable` reports as installed — the user would get a blank
  panel with no way to diagnose it.
"""
from __future__ import annotations

import io
import asyncio
import stat
import zipfile
from pathlib import Path

import pytest

from narranexus.platform.browser._browser_impl.downloader import (
    ChromiumDownloader,
    platform_key,
    resolve_build,
)

MANIFEST = {
    "channels": {
        "Stable": {
            "version": "152.0.7977.54",
            "downloads": {
                "chrome": [
                    {"platform": "mac-arm64", "url": "https://vendor.example/152/chrome-mac-arm64.zip"},
                    {"platform": "mac-x64", "url": "https://vendor.example/152/chrome-mac-x64.zip"},
                    {"platform": "linux64", "url": "https://vendor.example/152/chrome-linux64.zip"},
                ]
            },
        }
    }
}


# ── platform detection ───────────────────────────────────────────────────────


def test_platform_key_for_apple_silicon():
    assert platform_key(system="Darwin", machine="arm64") == "mac-arm64"


def test_platform_key_for_intel_mac():
    assert platform_key(system="Darwin", machine="x86_64") == "mac-x64"


def test_platform_key_for_linux():
    assert platform_key(system="Linux", machine="x86_64") == "linux64"


def test_unsupported_platform_is_named_not_guessed():
    """Silently downloading the wrong architecture wastes 150 MB and then
    fails with a confusing exec-format error."""
    with pytest.raises(ValueError, match="FreeBSD"):
        platform_key(system="FreeBSD", machine="riscv64")


# ── build resolution ─────────────────────────────────────────────────────────


def test_resolve_build_picks_this_platform():
    version, url = resolve_build(MANIFEST, plat="mac-arm64")
    assert version == "152.0.7977.54"
    assert url.endswith("chrome-mac-arm64.zip")


def test_resolve_build_reports_a_platform_the_vendor_does_not_publish():
    with pytest.raises(ValueError, match="win64"):
        resolve_build(MANIFEST, plat="win64")


def test_resolve_build_rejects_a_manifest_without_the_channel():
    with pytest.raises(ValueError, match="Stable"):
        resolve_build({"channels": {}}, plat="mac-arm64")


def test_mirror_host_rewrites_the_url_but_keeps_the_path():
    _v, url = resolve_build(MANIFEST, plat="mac-arm64", host="https://mirror.example.cn/cft")
    assert url.startswith("https://mirror.example.cn/cft/")
    assert url.endswith("152/chrome-mac-arm64.zip")


def test_plain_http_mirror_is_refused():
    """We execute what we download; a plaintext mirror lets anyone on the path
    swap the binary."""
    with pytest.raises(ValueError, match="https"):
        resolve_build(MANIFEST, plat="mac-arm64", host="http://mirror.example.cn")


# ── extraction ───────────────────────────────────────────────────────────────


def _zip_bytes(members: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def make_downloader(tmp_path: Path, archive: bytes, *, manifest=None) -> ChromiumDownloader:
    async def fetch_json(_url: str) -> dict:
        return manifest if manifest is not None else MANIFEST

    async def chunks(*, url: str, offset: int):  # noqa: ARG001
        yield archive[offset:], len(archive)

    return ChromiumDownloader(
        root=tmp_path, plat="mac-arm64", fetch_json=fetch_json, fetch_chunks=chunks
    )


@pytest.mark.asyncio
async def test_install_extracts_and_marks_the_binary_executable(tmp_path: Path):
    rel = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    dl = make_downloader(tmp_path, _zip_bytes({rel: b"#!/bin/sh\necho Chromium 152.0.7977.54\n"}))

    out = await dl(on_progress=lambda _p: None, cancelled=lambda: False)

    assert out.ok is True, out.error
    assert out.executable is not None
    assert out.executable.is_file()
    assert out.executable.stat().st_mode & 0o111, "must be executable after extraction"


@pytest.mark.asyncio
async def test_zip_slip_member_is_refused(tmp_path: Path):
    """A member escaping the install root must not be written anywhere."""
    evil = _zip_bytes({"../../pwned.txt": b"x"})
    dl = make_downloader(tmp_path, evil)

    out = await dl(on_progress=lambda _p: None, cancelled=lambda: False)

    assert out.ok is False
    assert not (tmp_path.parent / "pwned.txt").exists()
    assert not (tmp_path.parent.parent / "pwned.txt").exists()


@pytest.mark.asyncio
async def test_absolute_member_is_refused(tmp_path: Path):
    dl = make_downloader(tmp_path, _zip_bytes({"/etc/pwned": b"x"}))
    out = await dl(on_progress=lambda _p: None, cancelled=lambda: False)
    assert out.ok is False


@pytest.mark.asyncio
async def test_an_archive_without_the_expected_binary_is_a_failure(tmp_path: Path):
    """Extracting successfully is not installing successfully. Reporting ok
    here would leave `locate_executable` finding nothing and the user staring
    at an install button that already 'worked'."""
    dl = make_downloader(tmp_path, _zip_bytes({"chrome-mac-arm64/README": b"nothing here"}))

    out = await dl(on_progress=lambda _p: None, cancelled=lambda: False)

    assert out.ok is False
    assert "executable" in (out.error or "").lower()


@pytest.mark.asyncio
async def test_a_corrupt_archive_fails_cleanly(tmp_path: Path):
    dl = make_downloader(tmp_path, b"this is not a zip file")
    out = await dl(on_progress=lambda _p: None, cancelled=lambda: False)
    assert out.ok is False


@pytest.mark.asyncio
async def test_cancel_before_extraction_reports_not_ok_and_keeps_the_partial(tmp_path: Path):
    rel = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    dl = make_downloader(tmp_path, _zip_bytes({rel: b"x"}))

    out = await dl(on_progress=lambda _p: None, cancelled=lambda: True)

    assert out.ok is False
    assert out.error == "cancelled"


@pytest.mark.asyncio
async def test_progress_is_reported_during_download(tmp_path: Path):
    rel = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    seen = []
    dl = make_downloader(tmp_path, _zip_bytes({rel: b"x" * 500}))

    await dl(on_progress=seen.append, cancelled=lambda: False)

    assert seen, "the user needs to see something move during a 150 MB download"
    assert any(p.phase == "downloading" for p in seen)
    assert any(p.phase == "extracting" for p in seen)


@pytest.mark.asyncio
async def test_the_archive_is_removed_after_a_successful_install(tmp_path: Path):
    """Keeping a 150 MB zip next to the unpacked copy doubles the footprint
    for no benefit — a re-install re-downloads it."""
    rel = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
    dl = make_downloader(tmp_path, _zip_bytes({rel: b"#!/bin/sh\necho Chromium 152.0.7977.54\n"}))

    await dl(on_progress=lambda _p: None, cancelled=lambda: False)

    assert list(tmp_path.glob("*.zip")) == []


REL = "chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
SCRIPT = b"#!/bin/sh\necho 'Google Chrome for Testing 152.0.7977.54'\n"


@pytest.mark.asyncio
async def test_retry_resumes_real_downloader_from_retained_offset(tmp_path):
    payload = _zip_bytes({REL: SCRIPT})
    offsets = []

    async def manifest(_):
        return MANIFEST

    async def chunks(*, url, offset):
        offsets.append(offset)
        if len(offsets) == 1:
            yield payload[:50], len(payload)
            raise ConnectionError("interrupted")
        yield payload[offset:], len(payload)

    dl = ChromiumDownloader(root=tmp_path, plat="mac-arm64", fetch_json=manifest,
                            fetch_chunks=chunks)
    assert not (await dl(on_progress=lambda _: None, cancelled=lambda: False)).ok
    assert (await dl(on_progress=lambda _: None, cancelled=lambda: False)).ok
    assert offsets == [0, 50]


@pytest.mark.asyncio
async def test_broken_executable_is_not_published(tmp_path):
    dl = make_downloader(tmp_path, _zip_bytes({REL: b"#!/bin/sh\necho error\nexit 1\n"}))
    result = await dl(on_progress=lambda _: None, cancelled=lambda: False)
    assert not result.ok
    assert not (tmp_path / REL).exists()


@pytest.mark.asyncio
async def test_failed_install_cannot_reuse_an_old_executable(tmp_path):
    previous = tmp_path / REL
    previous.parent.mkdir(parents=True)
    previous.write_bytes(b"broken old browser")
    previous.chmod(0o755)
    dl = make_downloader(tmp_path, _zip_bytes({"chrome-mac-arm64/README": b"missing"}))
    result = await dl(on_progress=lambda _: None, cancelled=lambda: False)
    assert not result.ok
    assert previous.read_bytes() == b"broken old browser"


@pytest.mark.asyncio
async def test_cancel_during_extract_never_publishes_runtime(tmp_path):
    cancelled = False

    def progress(sample):
        nonlocal cancelled
        if sample.phase == "extracting":
            cancelled = True

    result = await make_downloader(tmp_path, _zip_bytes({REL: SCRIPT}))(
        on_progress=progress, cancelled=lambda: cancelled)
    assert result.error == "cancelled"
    assert not (tmp_path / REL).exists()


@pytest.mark.asyncio
async def test_macos_framework_symlinks_are_preserved(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(REL, SCRIPT)
        archive.writestr("chrome-mac-arm64/F.framework/Versions/1/F", b"framework")
        info = zipfile.ZipInfo("chrome-mac-arm64/F.framework/Versions/Current")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, "1")
    result = await make_downloader(tmp_path, buffer.getvalue())(
        on_progress=lambda _: None, cancelled=lambda: False)
    assert result.ok, result.error
    link = result.executable.parents[3] / "F.framework/Versions/Current"
    assert link.is_symlink()
    assert (link / "F").read_bytes() == b"framework"


@pytest.mark.asyncio
async def test_two_downloader_instances_share_one_install(tmp_path):
    started, finish = asyncio.Event(), asyncio.Event()
    calls = []
    payload = _zip_bytes({REL: SCRIPT})

    async def manifest(_):
        return MANIFEST

    async def chunks(*, url, offset):
        calls.append(offset)
        started.set()
        await finish.wait()
        yield payload[offset:], len(payload)

    first, second = [ChromiumDownloader(root=tmp_path, plat="mac-arm64", fetch_json=manifest,
                                       fetch_chunks=chunks) for _ in range(2)]
    task = asyncio.create_task(first(on_progress=lambda _: None, cancelled=lambda: False))
    await started.wait()
    other = asyncio.create_task(second(on_progress=lambda _: None, cancelled=lambda: False))
    await asyncio.sleep(0)
    finish.set()
    results = await asyncio.gather(task, other)
    assert all(result.ok for result in results)
    assert calls == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers", [
    (200, {"Content-Length": "4"}),
    (206, {"Content-Range": "bytes 0-3/8", "Content-Length": "4"}),
])
async def test_http_resume_refuses_ignored_or_incorrect_range(monkeypatch, status, headers):
    import httpx
    from narranexus.platform.browser._browser_impl.downloader import _http_chunks

    client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(status, headers=headers, content=b"abcd"))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=transport, **kw))
    with pytest.raises(OSError):
        _ = [chunk async for chunk in _http_chunks(url="https://example.test/browser", offset=4)]


@pytest.mark.parametrize("system,machine,expected", [
    ("Windows", "AMD64", "win64"), ("Windows", "x86", "win32"),
    ("Linux", "amd64", "linux64"), ("Darwin", "aarch64", "mac-arm64"),
])
def test_platform_aliases(system, machine, expected):
    assert platform_key(system=system, machine=machine) == expected


@pytest.mark.asyncio
async def test_http_resume_reports_whole_size_and_handles_complete_partial(monkeypatch):
    import httpx
    from narranexus.platform.browser._browser_impl.downloader import _http_chunks

    def respond(request):
        assert request.headers["accept-encoding"] == "identity"
        if request.headers["range"] == "bytes=8-":
            return httpx.Response(416, headers={"Content-Range": "bytes */8"})
        return httpx.Response(206, headers={"Content-Range": "bytes 4-7/8"}, content=b"efgh")

    client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    chunks = [item async for item in _http_chunks(url="https://example.test/browser", offset=4)]
    assert b"".join(chunk for chunk, _ in chunks) == b"efgh"
    assert all(total == 8 for _, total in chunks)
    assert [item async for item in _http_chunks(url="https://example.test/browser", offset=8)] == [(b"", 8)]


@pytest.mark.asyncio
async def test_corrupt_download_can_be_retried_without_appending_garbage(tmp_path):
    offsets = []

    async def manifest(_):
        return MANIFEST

    async def chunks(*, url, offset):
        offsets.append(offset)
        payload = b"bad archive" if len(offsets) == 1 else _zip_bytes({REL: SCRIPT})
        yield payload[offset:], len(payload)

    dl = ChromiumDownloader(root=tmp_path, plat="mac-arm64", fetch_json=manifest, fetch_chunks=chunks)
    assert not (await dl(on_progress=lambda _: None, cancelled=lambda: False)).ok
    assert (await dl(on_progress=lambda _: None, cancelled=lambda: False)).ok
    assert offsets == [0, 0]


@pytest.mark.asyncio
@pytest.mark.parametrize("link,child", [("../../escape", False), ("target", True)])
async def test_archive_links_cannot_escape_or_redirect_writes(tmp_path, link, child):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(REL, SCRIPT)
        info = zipfile.ZipInfo("chrome-mac-arm64/link")
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(info, link)
        if child:
            archive.writestr("chrome-mac-arm64/link/payload", b"redirected")
    result = await make_downloader(tmp_path, buffer.getvalue())(
        on_progress=lambda _: None, cancelled=lambda: False)
    assert not result.ok
    assert not (tmp_path / REL).exists()


@pytest.mark.asyncio
async def test_cancelled_extraction_waits_for_thread_before_unlocking(tmp_path, monkeypatch):
    import threading

    started, finish = threading.Event(), threading.Event()
    dl = make_downloader(tmp_path, _zip_bytes({REL: SCRIPT}))
    extract = dl._extract

    def blocked(*args):
        started.set()
        assert finish.wait(5)
        return extract(*args)

    monkeypatch.setattr(dl, "_extract", blocked)
    task = asyncio.create_task(dl(on_progress=lambda _: None, cancelled=lambda: False))
    assert await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0)
    assert dl.installing
    finish.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not dl.installing
    assert not (tmp_path / ".runtime-mac-arm64.json").exists()
    assert not list((tmp_path / "runtimes").iterdir())


@pytest.mark.asyncio
async def test_progress_and_cancel_are_shared_between_coordinators(tmp_path):
    from narranexus.platform.browser._browser_impl.install import InstallCoordinator

    seen = asyncio.Event()
    gate = asyncio.Event()
    payload = _zip_bytes({REL: SCRIPT})

    async def chunks(*, url, offset):
        yield payload[:50], len(payload)
        seen.set()
        await gate.wait()
        yield payload[50:], len(payload)

    async def manifest(_):
        return MANIFEST

    first = InstallCoordinator(downloader=ChromiumDownloader(
        root=tmp_path, plat="mac-arm64", fetch_json=manifest, fetch_chunks=chunks), is_ready=lambda: False)
    observer = InstallCoordinator(downloader=make_downloader(tmp_path, payload), is_ready=lambda: False)
    task = asyncio.create_task(first.install())
    await seen.wait()
    assert observer.installing
    assert observer.progress.bytes_done == 50
    observer.cancel()
    gate.set()
    assert (await task).error == "cancelled"
    assert not observer.installing


def test_install_lock_and_progress_cross_processes_and_survive_crash(tmp_path):
    import subprocess
    import sys
    from narranexus.platform.browser._browser_impl.install import _InstallState

    code = """
import sys
from pathlib import Path
from narranexus.platform.browser._browser_impl.install import _InstallState, _atomic_json
state = _InstallState(Path(sys.argv[1]))
assert state.acquire()
_atomic_json(state.state_path, {"token": "child", "progress": {"phase": "downloading", "bytes_done": 7, "bytes_total": 10}})
print("ready", flush=True)
sys.stdin.readline()
print(state.cancelled("child"), flush=True)
sys.stdin.readline()
"""
    process = subprocess.Popen([sys.executable, "-c", code, str(tmp_path)], stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == "ready"
        dl = make_downloader(tmp_path, b"")
        assert dl.installing
        assert dl.progress.bytes_done == 7
        dl.cancel()
        process.stdin.write("continue\n")
        process.stdin.flush()
        assert process.stdout.readline().strip() == "True"
    finally:
        process.kill()
        process.communicate(timeout=5)
    assert not dl.installing
    state = _InstallState(tmp_path)
    assert state.acquire()
    state.release()


@pytest.mark.asyncio
async def test_browser_version_must_match_the_manifest(tmp_path):
    dl = make_downloader(tmp_path, _zip_bytes({REL: b"#!/bin/sh\necho Chromium 1.2.3.4\n"}))
    result = await dl(on_progress=lambda _: None, cancelled=lambda: False)
    assert not result.ok
    assert "version" in result.error
