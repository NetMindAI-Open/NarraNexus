"""
@file_name: downloader.py
@author:
@date: 2026-09-22
@description: Install an optional Chrome for Testing runtime outside the app bundle.

Downloads resume only within the same version/platform/source. Extraction and
probing happen in an unpublished generation; a receipt becomes visible only
after verification. OS file locks serialize installers across app processes.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from typing import AsyncIterator, Awaitable, Callable, Optional
from urllib.parse import urlsplit

from loguru import logger

from narranexus.platform.browser._browser_impl.download import (
    DownloadCancelled,
    RestartDownload,
    await_cancellable,
    fetch_resumable,
    validate_https_url,
)
from narranexus.platform.browser._browser_impl.install import (
    InstallOutcome,
    InstallProgress,
    _atomic_json,
    _InstallState,
)
from narranexus.platform.browser._browser_impl.locate import (
    _CANDIDATE_RELPATHS,
    locate_executable,
    platform_key,
    probe_version,
)
from narranexus.platform.utils.file_safety import validate_zip_member_path

CFT_MANIFEST_URL = (
    "https://googlechromelabs.github.io/chrome-for-testing/"
    "last-known-good-versions-with-downloads.json"
)
DOWNLOAD_HOST_ENV = "NARRANEXUS_BROWSER_DOWNLOAD_HOST"
MANIFEST_URL_ENV = "NARRANEXUS_BROWSER_MANIFEST_URL"


def _apply_mirror(url: str, host: Optional[str]) -> str:
    validate_https_url(url)
    base = (host or "").strip()
    if not base:
        return url
    return f"{validate_https_url(base).rstrip('/')}{urlsplit(url).path}"


def resolve_build(
    manifest: dict, *, plat: str, channel: str = "Stable", host: Optional[str] = None,
) -> tuple[str, str]:
    """Use the vendor's published build rather than guessing revision URLs."""
    entry = (manifest.get("channels") or {}).get(channel)
    if not entry:
        raise ValueError(f"browser manifest has no {channel!r} channel")
    version = entry.get("version", "")
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){3}", version):
        raise ValueError("browser manifest has an invalid version")
    for item in (entry.get("downloads") or {}).get("chrome") or []:
        if item.get("platform") == plat:
            return version, _apply_mirror(str(item["url"]), host)
    raise ValueError(f"browser manifest publishes no build for platform {plat!r}")


class ChromiumDownloader:
    """Resolve, resume, stage, probe and atomically publish a browser install."""

    def __init__(
        self, *, root: Path, plat: Optional[str] = None, host: Optional[str] = None,
        manifest_url: Optional[str] = None,
        fetch_json: Optional[Callable[[str], Awaitable[dict]]] = None,
        fetch_chunks: Optional[Callable[..., AsyncIterator[tuple[bytes, Optional[int]]]]] = None,
    ) -> None:
        self._root = root
        self._plat = plat
        self._host = host if host is not None else os.environ.get(DOWNLOAD_HOST_ENV)
        self._manifest_url = manifest_url
        self._fetch_json = fetch_json or _http_json
        self._fetch_chunks = fetch_chunks or _http_chunks

    @property
    def installing(self) -> bool:
        return _InstallState(self._root).active

    @property
    def progress(self) -> Optional[InstallProgress]:
        state = _InstallState(self._root)
        if state.active:
            sample = state.read().get("progress")
            if isinstance(sample, dict):
                try:
                    return InstallProgress(**sample)
                except TypeError:
                    pass
        return None

    def cancel(self) -> None:
        _InstallState(self._root).cancel()

    async def __call__(
        self, *, on_progress: Callable[[InstallProgress], None], cancelled: Callable[[], bool],
    ) -> InstallOutcome:
        state = _InstallState(self._root)
        token = uuid.uuid4().hex
        outcome = InstallOutcome(False, None, "install interrupted")
        try:
            waited = False
            while not state.acquire():
                waited = True
                if cancelled():
                    state.cancel()
                    return InstallOutcome(False, None, "cancelled")
                sample = self.progress
                if sample is not None:
                    _report(on_progress, sample)
                await asyncio.sleep(0.1)
            if waited:
                previous = state.read().get("outcome")
                if isinstance(previous, dict):
                    executable = previous.get("executable")
                    return InstallOutcome(previous["ok"], Path(executable) if executable else None,
                                          previous.get("error"))
            _atomic_json(state.state_path, {"token": token})
            last_report = 0.0
            last_phase = None

            def report(sample: InstallProgress) -> None:
                nonlocal last_report, last_phase
                now = time.monotonic()
                if sample.phase != last_phase or now - last_report >= 0.1:
                    _atomic_json(state.state_path, {"token": token, "progress": asdict(sample)})
                    last_report, last_phase = now, sample.phase
                _report(on_progress, sample)

            def stopped() -> bool:
                return cancelled() or state.cancelled(token)

            # Cross-process idempotency is checked only while holding the lock.
            plat = self._plat or platform_key()
            existing = locate_executable(root=self._root, plat=plat)
            if existing is not None and await _thread(probe_version, existing):
                outcome = InstallOutcome(True, existing, None)
            elif stopped():
                outcome = InstallOutcome(False, None, "cancelled")
            else:
                outcome = await self._install(plat, report, stopped)
            return outcome
        except asyncio.CancelledError:
            outcome = InstallOutcome(False, None, "cancelled")
            raise
        except DownloadCancelled:
            outcome = InstallOutcome(False, None, "cancelled")
            return outcome
        except Exception as exc:
            logger.exception("browser install failed")
            outcome = InstallOutcome(False, None, str(exc))
            return outcome
        finally:
            if state.handle is not None:
                try:
                    # A waiter returning a previous result must not overwrite it.
                    if state.read().get("token") == token:
                        _atomic_json(state.state_path, {
                            "token": token,
                            "outcome": {"ok": outcome.ok, "error": outcome.error,
                                        "executable": str(outcome.executable) if outcome.executable else None},
                        })
                finally:
                    state.release()

    async def _install(self, plat, report, cancelled) -> InstallOutcome:
        manifest_url = (self._manifest_url or os.environ.get(MANIFEST_URL_ENV) or CFT_MANIFEST_URL).strip()
        manifest = await await_cancellable(self._fetch_json(validate_https_url(manifest_url)), cancelled)
        version, url = resolve_build(manifest, plat=plat, host=self._host)
        # Include the URL: changing mirrors must never append unrelated bytes.
        identity = hashlib.sha256(f"{plat}\n{version}\n{url}".encode()).hexdigest()[:24]
        archive = self._root / f"download-{identity}.zip"

        def source(*, offset):
            return self._fetch_chunks(url=url, offset=offset)

        if cancelled():
            return InstallOutcome(False, None, "cancelled")
        try:
            done = await fetch_resumable(dest=archive, source=source, on_progress=report,
                                         cancelled=cancelled)
        except RestartDownload:
            archive.unlink(missing_ok=True)
            done = await fetch_resumable(dest=archive, source=source, on_progress=report,
                                         cancelled=cancelled)
        if cancelled():
            return InstallOutcome(False, None, "cancelled")

        generations = self._root / "runtimes"
        generations.mkdir(exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=f"{plat}-{version}-", dir=generations))
        published = False
        try:
            report(InstallProgress("extracting", done, done))
            if cancelled():
                return InstallOutcome(False, None, "cancelled")
            try:
                await _thread(self._extract, archive, staging, cancelled)
            except (zipfile.BadZipFile, ValueError):
                # A complete but corrupt/unsafe archive can never be resumed.
                archive.unlink(missing_ok=True)
                raise
            if cancelled():
                return InstallOutcome(False, None, "cancelled")
            report(InstallProgress("verifying", done, done))
            executable = locate_executable(root=staging, plat=plat)
            if executable is None:
                archive.unlink(missing_ok=True)
                raise ValueError("the archive unpacked but no browser executable was found in it")
            detected = await _thread(probe_version, executable)
            if not detected:
                archive.unlink(missing_ok=True)
                raise ValueError("the browser executable failed its --version probe")
            if detected.rsplit(" ", 1)[-1] != version:
                archive.unlink(missing_ok=True)
                raise ValueError("the browser version does not match the download manifest")
            if cancelled():
                return InstallOutcome(False, None, "cancelled")
            _atomic_json(self._root / f".runtime-{plat}.json", {
                "version": version, "executable": str(executable.relative_to(self._root)),
            })
            published = True
            archive.unlink(missing_ok=True)
            return InstallOutcome(True, executable, None)
        finally:
            if not published:
                shutil.rmtree(staging)

    def _extract(self, archive: Path, destination: Path, cancelled: Callable[[], bool]) -> None:
        """Preserve framework symlinks without letting them redirect file writes."""
        with zipfile.ZipFile(archive) as zf:
            entries = []
            links: dict[PurePosixPath, str] = {}
            seen = set()
            for info in zf.infolist():
                relative = validate_zip_member_path(info.filename)
                if any(":" in part for part in relative.parts) or relative in seen:
                    raise ValueError("invalid or duplicate browser archive path")
                seen.add(relative)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if kind == stat.S_IFLNK:
                    target = zf.read(info).decode("utf-8")
                    if (not target or "\\" in target or ":" in target or "\0" in target
                            or Path(target).is_absolute()):
                        raise ValueError("invalid browser archive symlink")
                    resolved = (destination / relative.parent / target).resolve()
                    if not resolved.is_relative_to(destination.resolve()):
                        raise ValueError("browser archive symlink escapes install directory")
                    links[relative] = target
                elif kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise ValueError("unsupported browser archive file type")
                entries.append((info, relative, mode))
            for _, relative, _ in entries:
                if any(parent in links for parent in relative.parents):
                    raise ValueError("browser archive writes through a symlink")
            for info, relative, mode in entries:
                if cancelled():
                    return
                target = destination / relative
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                elif relative not in links:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as source, target.open("xb") as output:
                        while chunk := source.read(1 << 20):
                            if cancelled():
                                return
                            output.write(chunk)
                    target.chmod((mode & 0o777) or 0o644)
            for relative, link in links.items():
                target = destination / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
            # Validate chains as well as individual relative targets.
            for relative in links:
                try:
                    resolved = (destination / relative).resolve(strict=True)
                except (OSError, RuntimeError) as exc:
                    raise ValueError("broken browser archive symlink") from exc
                if not resolved.is_relative_to(destination.resolve()):
                    raise ValueError("browser archive symlink escapes install directory")
        for parts in (*_CANDIDATE_RELPATHS, ("chrome-win32", "chrome.exe")):
            candidate = destination.joinpath(*parts)
            if candidate.is_file():
                candidate.chmod(candidate.stat().st_mode | 0o755)


def _report(listener, sample) -> None:
    try:
        listener(sample)
    except Exception:
        logger.exception("browser install: progress listener raised")


async def _thread(function, *args):
    # Cancellation does not stop a thread. Keep the install lock and staging
    # directory alive until the worker exits, even if its caller disconnects.
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise


async def _http_json(url: str) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        res = await client.get(url)
        validate_https_url(str(res.url))
        res.raise_for_status()
        return res.json()


async def _http_chunks(*, url: str, offset: int) -> AsyncIterator[tuple[bytes, Optional[int]]]:
    import httpx

    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    async with httpx.AsyncClient(timeout=httpx.Timeout(30, read=30), follow_redirects=True) as client:
        async with client.stream("GET", url, headers=headers) as res:
            validate_https_url(str(res.url))
            if offset and res.status_code == 416:
                if res.headers.get("content-range") == f"bytes */{offset}":
                    yield b"", offset
                    return
                raise RestartDownload("browser partial exceeds the remote archive size")
            res.raise_for_status()
            if res.headers.get("content-encoding", "identity") != "identity":
                raise OSError("browser download response must not use content encoding")
            declared = res.headers.get("content-length")
            total = int(declared) if declared and declared.isdigit() else None
            if res.status_code == 206:
                match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", res.headers.get("content-range", ""))
                if match is None:
                    raise RestartDownload("invalid browser download Content-Range")
                start, end, whole = map(int, match.groups())
                if start != offset or end < start or end != whole - 1 or (
                    total is not None and total != end - start + 1
                ):
                    raise RestartDownload("incorrect browser download Content-Range")
                total = whole
            elif offset:
                raise RestartDownload("browser download server ignored the Range request")
            elif res.status_code != 200:
                raise OSError(f"unexpected browser download status: {res.status_code}")
            yield b"", total
            async for chunk in res.aiter_bytes(chunk_size=1 << 16):
                yield chunk, total
