"""
@file_name: download.py
@author:
@date: 2026-09-22
@description: Resolve where the browser runtime comes from, and fetch it resumably.

Two requirements here are about real users rather than about correctness in
the abstract:

**The mirror host must be overridable.** Local-mode users in mainland China
are the main population for this product, not an edge case. If
the download host cannot be pointed somewhere they can reach, the browser
feature simply does not exist for them. The override is therefore a first
class argument, not an afterthought — and it is refused unless it is HTTPS,
because a plain-http mirror lets anyone on the path swap the binary we are
about to execute.

**Resume is not optional.** A ~150 MB download over a flaky link that restarts
from zero every attempt never finishes. The fetch loop range-requests from
whatever is already on disk, and every failure path leaves the partial file
intact so the next attempt gains from it.

The HTTP layer is injected as ``source`` so both concerns can be tested
deterministically and so the transport can change without touching this logic.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Callable, Optional, Protocol
from urllib.parse import urlsplit

from narranexus.platform.browser._browser_impl.install import InstallProgress

#: Vendor default. Overridden per-install by the mirror host.
DEFAULT_DOWNLOAD_HOST = "https://playwright.azureedge.net/builds/chromium"


def validate_https_url(url: str) -> str:
    """Refuse malformed or credential-bearing download and mirror URLs."""
    parsed = urlsplit(url)
    if (parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("browser download URL must be an absolute https URL without credentials, query or fragment")
    _ = parsed.port
    return url


class RestartDownload(OSError):
    """The server cannot continue this partial; retry from byte zero."""


class DownloadCancelled(Exception):
    """Cooperative cancellation of an outstanding network operation."""


async def await_cancellable(awaitable, cancelled):
    """Close stalled requests promptly when the user cancels installation."""
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            if cancelled():
                raise DownloadCancelled("cancelled")
            done, _ = await asyncio.wait({task}, timeout=0.1)
            if done:
                return task.result()
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@dataclass(frozen=True)
class DownloadSpec:
    """Which build to fetch. Revision + archive name identify it exactly."""

    revision: str
    archive: str


def resolve_download_url(*, spec: DownloadSpec, host: Optional[str] = None) -> str:
    """Build the archive URL, honouring a mirror override.

    Args:
        spec: Which build to fetch.
        host: Mirror base URL. Blank / None means "unset" and falls back to
            the vendor default — an env var set to the empty string is not a
            request to download from nowhere.

    Returns:
        The absolute archive URL.

    Raises:
        ValueError: If the override is not HTTPS. We execute what we download;
            refusing is the only safe answer, and silently downgrading to the
            default would hide a misconfigured mirror from whoever set it.
    """
    base = (host or "").strip()
    if not base:
        base = DEFAULT_DOWNLOAD_HOST
    validate_https_url(base)
    return f"{base.rstrip('/')}/{spec.revision}/{spec.archive}"


class ChunkSource(Protocol):
    """Yields ``(chunk, total_bytes)`` starting at ``offset``.

    ``total_bytes`` is the size of the WHOLE artifact (not the remainder) so
    resumed progress can be reported against the real total. It may be None
    when the server answers without ``Content-Length``.
    """

    def __call__(self, *, offset: int) -> AsyncIterator[tuple[bytes, Optional[int]]]: ...


async def fetch_resumable(
    *,
    dest: Path,
    source: ChunkSource,
    on_progress: Callable[[InstallProgress], None],
    cancelled: Callable[[], bool],
) -> int:
    """Append to ``dest`` from wherever it already ends. Returns total bytes on disk.

    Progress counts bytes **already on disk**, not bytes fetched this attempt:
    a bar that restarts at zero when resuming reads as "it lost my download",
    which is exactly the moment users give up and delete the partial file.

    Cancellation is checked between chunks and returns normally with the
    partial kept — a cancel is not an error, and throwing away the bytes
    would punish the user for changing their mind.

    Any transport exception propagates, but only after what arrived is
    flushed: the whole point of the partial file is that the next attempt
    starts from it.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    offset = dest.stat().st_size if dest.exists() else 0
    if cancelled():
        return offset

    total = None
    iterator = source(offset=offset)
    try:
        with dest.open("ab") as fh:
            while True:
                try:
                    chunk, total = await await_cancellable(anext(iterator), cancelled)
                except StopAsyncIteration:
                    break
                except DownloadCancelled:
                    return offset
                if cancelled():
                    return offset
                if total is not None and offset + len(chunk) > total:
                    raise RestartDownload("browser response exceeds its declared size")
                fh.write(chunk)
                # Preserve resumable bytes even when a request is interrupted.
                fh.flush()
                offset += len(chunk)
                on_progress(InstallProgress("downloading", offset, total))
                if cancelled():
                    return offset
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()

    if total is not None and offset != total:
        raise OSError(f"incomplete browser download: received {offset} of {total} bytes")
    return offset
