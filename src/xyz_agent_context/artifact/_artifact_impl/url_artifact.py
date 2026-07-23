"""
@file_name: url_artifact.py
@author: Bin Liang
@date: 2026-07-22
@description: Create / update URL-tab artifacts (application/x-url).

A URL tab is an ordinary pointer artifact whose entry file is a small JSON
doc (UrlArtifactDoc) at `tabs/<slug>/page.url.json` in the agent workspace.
This module owns the doc I/O + orchestration; registration itself goes through
the shared `registration.register_artifact` so URL tabs get heal / delete /
bundle / raw-serving for free.

Each URL tab lives in its OWN subdirectory so the raw route's artifact-root
isolation means one tab can never read another tab's json.

No DB column for the URL — the JSON doc is the source of truth (pointer model
preserved). Updating the embed decision = rewriting the doc.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
from typing import Optional
from urllib.parse import urlparse

from loguru import logger

from xyz_agent_context.artifact._artifact_impl import registration
from xyz_agent_context.artifact._artifact_impl.embed_probe import probe_url
from xyz_agent_context.artifact._artifact_impl.errors import (
    ArtifactContentGone,
    ArtifactError,
    ArtifactNotFound,
)
from xyz_agent_context.artifact._artifact_impl.page_text import fetch_page_text
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import (
    URL_ARTIFACT_KIND,
    URL_TAB_CONTENT_FILENAME,
    Artifact,
    CreateArtifactToolResult,
    EmbedMode,
    EmbedVerdict,
    UrlArtifactDoc,
)
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.url_safety import UnsafeUrlError, assert_public_http_url

_URL_TABS_DIR = "tabs"
_DOC_FILENAME = "page.url.json"
# Total wall-clock budget for the two outbound fetches (probe + page text) done
# while opening a URL tab. Bounds how long this MCP tool blocks — an
# outbound-HTTP deadline, NOT an agent_loop ceiling (铁律 #14 untouched).
_OPEN_FETCH_BUDGET_S = 20.0


def _our_scheme() -> str:
    """The scheme WE are served on, for the mixed-content check. Cloud is
    https; local dev is http. Derived from settings.public_base_url when set,
    else assumed http (local run.sh)."""
    base = (settings.public_base_url or "").strip().lower()
    if base.startswith("https://"):
        return "https"
    return "http"


def _doc_abs_path(agent_id: str, user_id: str, rel_entry: str) -> str:
    """Absolute path of a URL-tab doc. `rel_entry` is relative to the AGENT
    WORKSPACE root (the same base registration._resolve_entry joins against),
    NOT to base_working_path — the nested layout lives under the workspace."""
    workspace = registration.workspace_root(agent_id, user_id)
    return os.path.join(workspace, rel_entry)


def _read_doc(abs_path: str) -> UrlArtifactDoc:
    # A missing doc is a REAL state under the pointer model (the agent can
    # move/delete workspace files) — surface it as 410, not a bare
    # FileNotFoundError that the route would turn into a 500 + traceback.
    try:
        with open(abs_path, "r", encoding="utf-8") as f:
            return UrlArtifactDoc.model_validate_json(f.read())
    except FileNotFoundError as e:
        raise ArtifactContentGone("URL-tab doc is missing on disk") from e


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin_tuple(url: str) -> Optional[tuple[str, str, Optional[int]]]:
    """(scheme, host, effective_port) normalized the way a BROWSER compares
    origins: scheme + host lowercased, userinfo dropped, the default port for
    the scheme filled in. Returns None when there is no host to compare.

    This is the crux of the self-origin guard: a naive `netloc == netloc`
    string compare is bypassable (`AGENT.narra.nexus`, `host:443`,
    `u@host` all read as same-origin to the browser but differ as strings).
    """
    p = urlparse(url)
    host = (p.hostname or "").lower()
    if not host:
        return None
    scheme = (p.scheme or "").lower()
    try:
        port = p.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None  # malformed port
    return (scheme, host, port)


def _reject_self_origin(url: str, *, extra_origins: tuple[str, ...] = ()) -> None:
    """Refuse a URL whose browser-origin equals our own app's.

    A same-origin URL tab would become a same-origin iframe; with the
    renderer's `allow-same-origin allow-scripts` sandbox that iframe could
    reach the parent app's DOM / localStorage token and drive the app's API as
    the user. The SSRF gate only blocks private addresses — our own public
    origin is public — so this is the guard that keeps the `allow-same-origin`
    grant safe (a URL tab is only ever meant to hold cross-origin third-party
    content).

    Candidate origins: `settings.public_base_url` (set in cloud; the MCP path
    has only this) plus any `extra_origins` the caller derived from the request
    (the HTTP route passes the browser-visible origin, so the guard holds even
    if public_base_url is misconfigured). Empty candidates are skipped — local
    dev without a configured origin has no shared tenant and its localhost URLs
    are already rejected by the SSRF loopback rule.
    """
    target = _origin_tuple(url)
    if target is None:
        return
    for candidate in (settings.public_base_url, *extra_origins):
        candidate = (candidate or "").strip()
        if not candidate:
            continue
        ours = _origin_tuple(candidate)
        if ours is not None and ours == target:
            raise ArtifactError("refusing to open a URL tab pointing at this app's own origin")


def _write_doc(abs_path: str, doc: UrlArtifactDoc) -> None:
    # Atomic write: a truncate-in-place crash (e.g. during a set_embed_mode
    # toggle) would leave a half-written doc that _read_doc can't parse,
    # bricking the tab. Write a sibling temp file then os.replace (atomic on
    # POSIX) so a reader always sees either the old or the new complete doc.
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    tmp_path = f"{abs_path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(doc.model_dump_json(indent=2))
    os.replace(tmp_path, abs_path)


def _write_content(dir_abs: str, *, url: str, title: str, page_text: Optional[str]) -> None:
    """Write the agent-readable `content.md` next to the doc. Always written
    (even when extraction failed) so the state-block hint always points at a
    real file. The body is a snapshot taken at open time."""
    if page_text:
        body = page_text
    else:
        body = (
            "_The page text could not be captured automatically (the site may "
            "block server-side fetches, require login, or render entirely with "
            "JavaScript). Ask the user what they need from this page._"
        )
    content = f"# {title}\n\nSource: {url}\n\n---\n\n{body}\n"
    abs_path = os.path.join(dir_abs, URL_TAB_CONTENT_FILENAME)
    os.makedirs(dir_abs, exist_ok=True)
    tmp_path = f"{abs_path}.{os.getpid()}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        f.write(content)
    os.replace(tmp_path, abs_path)


async def open_url(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    user_id: str,
    session_id: Optional[str],
    url: str,
    title: Optional[str] = None,
    app_origin: Optional[str] = None,
) -> CreateArtifactToolResult:
    """Create a URL-tab artifact.

    Steps:
    1. Reject a URL whose browser-origin is our own app (self-origin guard,
       keeps the renderer's allow-same-origin sandbox safe) and hard-reject an
       internal URL (SSRF gate). Both raise ArtifactError — creation fails loud.
    2. Probe the URL server-side for its embed verdict (never crashes; a
       failed probe degrades to an optimistic iframe verdict).
    3. Write the UrlArtifactDoc into a dedicated `tabs/<slug>/` subdir.
    4. Register it through the shared pointer path.

    Args:
        app_origin: the browser-visible origin of the app, derived from the
            request by the HTTP route (the MCP path leaves it None and relies
            on settings.public_base_url). Used only to widen the self-origin
            guard's defense.

    Raises:
        ArtifactError: URL is our own origin, or not a safe public http(s) target.
    """
    _reject_self_origin(url, extra_origins=(app_origin,) if app_origin else ())
    try:
        await assert_public_http_url(url)
    except UnsafeUrlError as e:
        raise ArtifactError(f"refusing to open a non-public URL: {e}") from e

    # Probe (embed verdict) and page-text capture are independent outbound
    # fetches — run them CONCURRENTLY under one wall-clock budget so opening a
    # tab isn't the sum of both worst cases. Both degrade rather than raise;
    # on timeout we fall back to an optimistic iframe verdict + no text.
    verdict: EmbedVerdict
    page_text: Optional[str]
    try:
        async with asyncio.timeout(_OPEN_FETCH_BUDGET_S):
            verdict, page_text = await asyncio.gather(
                probe_url(url, our_scheme=_our_scheme()),
                fetch_page_text(url),
            )
    except TimeoutError:
        logger.info("open_url: outbound fetch budget exceeded for {}", url)
        verdict = EmbedVerdict(recommended="iframe", reason="probe-failed", probe_status="failed")
        page_text = None

    slug = secrets.token_hex(4)
    rel_entry = f"{_URL_TABS_DIR}/{slug}/{_DOC_FILENAME}"
    abs_entry = _doc_abs_path(agent_id, user_id, rel_entry)
    resolved_title = (title or url)[:200]
    _write_doc(abs_entry, UrlArtifactDoc(url=url, title=resolved_title, embed=verdict))
    # Sibling file the agent can Read (surfaced in the artifact state block).
    _write_content(os.path.dirname(abs_entry), url=url, title=resolved_title, page_text=page_text)

    result = await registration.register_artifact(
        repo=repo,
        agent_id=agent_id,
        user_id=user_id,
        session_id=session_id,
        kind=URL_ARTIFACT_KIND,  # type: ignore[arg-type]
        entry_path=rel_entry,
        title=resolved_title,
        description=url,  # human-readable pointer target for listings
        target_artifact_id=None,
    )
    logger.debug("Opened URL artifact {} -> {}", result.artifact_id, url)
    return result


async def set_embed_mode(
    *,
    repo: ArtifactRepository,
    agent_id: str,
    artifact_id: str,
    mode: Optional[EmbedMode],
) -> Artifact:
    """Set (or clear, with mode=None) the user's manual embed override on a
    URL tab, by rewriting its JSON doc. Returns the artifact row.

    Raises:
        ArtifactNotFound: artifact missing / not this agent's / not a URL tab.
    """
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id or art.kind != URL_ARTIFACT_KIND:
        raise ArtifactNotFound("URL artifact not found")

    from xyz_agent_context.utils.workspace_paths import resolve_workspace_relative_file

    base = os.path.realpath(settings.base_working_path)
    abs_entry = os.path.realpath(
        str(resolve_workspace_relative_file(art.file_path, art.agent_id, art.user_id, base))
    )
    doc = _read_doc(abs_entry)
    if doc.embed is None:
        # No probe verdict on disk (shouldn't happen for a real URL tab, but be
        # defensive): synthesize an iframe default to hang the override on.
        doc.embed = EmbedVerdict(recommended="iframe", reason="no-blocking-headers")
    doc.embed.user_override = mode
    _write_doc(abs_entry, doc)

    # Bump updated_at so the frontend's refetch sees a change. update_title with
    # the unchanged title is a deliberate no-op-content / touch-timestamp write
    # (the repo has no bare `touch`); the override itself lives in the doc, not
    # the DB row.
    await repo.update_title(artifact_id, art.title)
    refreshed = await repo.get_by_id(artifact_id)
    assert refreshed is not None
    return refreshed
