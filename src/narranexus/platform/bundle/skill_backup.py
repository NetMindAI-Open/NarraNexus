"""
@file_name: skill_backup.py
@author: NetMind.AI
@date: 2026-05-08
@description: Skill archive helpers — used by MCP tools and the install API

Subproject 2 §8.12.2 ~ §8.12.5.
"""

import io
import os
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from loguru import logger

from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.repository import SkillArchiveRepository
from .security import bytes_sha256, file_sha256, validate_skill_archive_path
from .skill_secrets import dir_is_builtin as _dir_is_builtin
from narranexus.platform.utils.file_safety import (
    ensure_within_directory,
    sanitize_filename,
)


SKILL_ARCHIVES_ROOT = Path.home() / ".nexusagent" / "skill_archives"
# SINGLE-WORKER ASSUMPTION: archive_path columns are absolute local fs paths.
# Multi-pod scale needs shared volume or object-store URLs — see
# .mindflow/project/references/scaling_assumptions.md §2.


def _user_archive_dir(user_id: str) -> Path:
    """Path of a user's archive dir. Pure — does NOT create it."""
    # `user_id` is a path segment. It comes from JWT / X-User-Id resolution
    # rather than a form field, but "trusted enough" is how SEC-07 happened
    # one level down — validate it like any other segment.
    return SKILL_ARCHIVES_ROOT / sanitize_filename(user_id, label="user id")


def archive_target(user_id: str, skill_name: str, *, suffix: str = ".zip") -> Path:
    """
    Resolve the on-disk archive path for (user, skill) — the ONLY sanctioned
    way to build a path under `skill_archives/`.

    SEC-07: `skill_name` always originates outside the process — a multipart
    Form field (`/skills/archives/upload`), a bundle manifest written by
    whoever produced the `.nxbundle`, or an LLM-supplied MCP tool argument.
    Splicing it into an f-string let `../` escape the per-user directory and
    write into another user's, which is a proven cross-user file write.

    Pure: validates and computes, touches nothing. That is what lets a caller
    validate up front and still promise "a rejected request leaves no trace" —
    use `prepare_archive_target` / `ensure_archive_dir` at the actual write.

    Args:
        user_id: Owning user; becomes the parent directory segment.
        skill_name: Untrusted skill name; must be a single path segment.
        suffix: Extension to append (".zip", ".tar.gz", "_full.zip").

    Returns:
        Absolute path inside `skill_archives/{user_id}/`.

    Raises:
        ValueError: On traversal, path separators, empty/dot names, null
            bytes, or a symlinked user dir that escapes the archives root.
            Callers exposing HTTP must map this to 4xx, not 500.
    """
    safe_name = sanitize_filename(skill_name, label="skill name")
    target = ensure_within_directory(
        _user_archive_dir(user_id), f"{safe_name}{suffix}", label="skill name"
    )
    # Second, DELIBERATELY DIFFERENT anchor. `ensure_within_directory` above
    # anchors on the user dir, so a user dir that is itself a symlink out of
    # the tree yields a path that is "contained" yet outside. This check
    # anchors on the archives root, which a symlinked user dir cannot satisfy.
    # Do NOT "unify" it with the per-user read-side check
    # (`is_within_user_archive_dir`): anchored on the already-resolved user dir
    # it would be vacuously true and the symlink hole would reopen.
    if not is_within_archives_root(target):
        raise ValueError("Invalid skill name: path escapes the skill archives root")
    return target


def ensure_archive_dir(target: Path) -> Path:
    """Create `target`'s parent dir. Call this at the write, not at validation."""
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def prepare_archive_target(user_id: str, skill_name: str, *, suffix: str = ".zip") -> Path:
    """`archive_target` + create the parent dir. For callers about to write."""
    return ensure_archive_dir(archive_target(user_id, skill_name, suffix=suffix))


def is_within_archives_root(archive_path: str | Path) -> bool:
    """
    Is `archive_path` anywhere under `skill_archives/`?

    Write-side use only (see `archive_target`). This is the LOOSER of the two
    containment checks: it says nothing about *whose* directory the path lands
    in, so `{root}/{someone_else}/x.zip` and `{root}/stray.zip` both pass. Read
    sides that know the owning user must use `is_within_user_archive_dir`.
    """
    try:
        resolved = Path(archive_path).resolve(strict=False)
        return resolved.is_relative_to(SKILL_ARCHIVES_ROOT.resolve(strict=False))
    except (OSError, ValueError):
        return False


def is_within_user_archive_dir(user_id: str, archive_path: str | Path) -> bool:
    """
    Read-side containment check for an `archive_path` DB column: does it point
    inside THIS user's own archive dir?

    Sealing the write path does not clean rows written before it was sealed —
    the dev env still carries the QA repro's row, whose stored string is
    `{root}/{uid}/../qa-sec07-oneup-marker.zip` and therefore *resolves into
    the root* (`{root}/qa-sec07-oneup-marker.zip`). A root-anchored check
    passes that row, so the per-user anchor is what actually catches it, plus
    the `{root}/{victim}/x.zip` shape. `builder.py` copies whatever
    `archive_path` names into the bundle it streams back, so every DB-sourced
    archive path must come through here before being opened.

    Both anchors must hold. The per-user one does the real work here; the root
    one is kept because `base` is itself resolved, so a `{root}/{user_id}`
    symlink pointing out of the tree would make "inside base" true while being
    outside everything. The write side rejects such a user dir, but read-side
    depth should not depend on that staying true.
    """
    try:
        base = _user_archive_dir(user_id).resolve(strict=False)
        return is_within_archives_root(archive_path) and Path(
            archive_path
        ).resolve(strict=False).is_relative_to(base)
    except (OSError, ValueError):
        return False


def _agent_workspace_root(agent_id: str, user_id: str) -> Optional[Path]:
    """Resolve canonical workspace dir; fall back to legacy `_user_` infix."""
    from narranexus.platform.settings import settings as core_settings

    # Realpath the platform-owned base only; the tenant tail stays lexical so
    # resolve_inside_workspace can compare a resolved candidate against it.
    base = Path(os.path.realpath(core_settings.base_working_path))
    from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

    candidates = [
        base / agent_workspace_relpath(agent_id, user_id),  # canonical (current layout)
        base / f"{agent_id}_{user_id}",  # legacy flat (pre-nested migration)
        base / f"{agent_id}_user_{user_id}",  # legacy _user_ infix
    ]
    for c in candidates:
        if c.is_dir():
            return c
    return None


async def archive_github_tarball(
    user_id: str,
    skill_name: str,
    github_url: str,
    branch: str = "main",
    github_token: Optional[str] = None,
) -> Tuple[Path, str]:
    """Download GitHub tarball and store as the skill's archive.

    Public repos work over the unauthenticated tarball URL. Private repos
    require a personal access token; either pass it explicitly via
    `github_token` or set the GITHUB_TOKEN env var. Token is sent via
    `Authorization: Bearer …` and is NOT persisted anywhere.

    Returns (archive_path, sha256).
    """
    import os

    p = urlparse(github_url)
    if p.scheme != "https" or p.hostname not in {"github.com", "www.github.com"}:
        raise ValueError("Only https://github.com/<owner>/<repo> is supported")
    parts = [s for s in (p.path or "").split("/") if s]
    if len(parts) < 2:
        raise ValueError("Invalid GitHub URL")
    owner, repo = parts[0], parts[1].removesuffix(".git")

    # GitHub's API tarball endpoint works for both public AND private repos
    # when paired with an Authorization header. The /archive/refs/heads/...
    # form is public-only.
    out_path = prepare_archive_target(user_id, skill_name, suffix=".tar.gz")
    token = github_token or os.environ.get("GITHUB_TOKEN") or ""
    headers = {"Accept": "application/vnd.github.v3.raw"}
    if token:
        # Use API endpoint with auth — works for private repos too.
        tarball_url = f"https://api.github.com/repos/{owner}/{repo}/tarball/{branch}"
        headers["Authorization"] = f"Bearer {token}"
    else:
        # Public-only path — no auth, simpler.
        tarball_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.tar.gz"

    async with httpx.AsyncClient(follow_redirects=True, timeout=120) as client:
        resp = await client.get(tarball_url, headers=headers)
        if resp.status_code == 404 and not token:
            raise ValueError(
                f"Tarball 404 for {github_url} (branch={branch}). "
                "If this is a private repo, set GITHUB_TOKEN or pass github_token."
            )
        if resp.status_code != 200:
            raise ValueError(f"Failed to download tarball: HTTP {resp.status_code} ({tarball_url})")
        out_path.write_bytes(resp.content)
    sha = file_sha256(out_path)
    logger.info(f"GitHub tarball archived for '{skill_name}': {out_path}")
    return out_path, sha


async def archive_md_only(
    user_id: str,
    skill_name: str,
    skill_md_content: str,
) -> Tuple[Path, str]:
    """Wrap a single SKILL.md content into a zip and store it as the archive."""
    out_path = prepare_archive_target(user_id, skill_name)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", skill_md_content)
    payload = buf.getvalue()
    out_path.write_bytes(payload)
    sha = bytes_sha256(payload)
    logger.info(f"MD-only archive created for '{skill_name}': {out_path}")
    return out_path, sha


async def archive_local_zip(
    user_id: str,
    agent_id: str,
    skill_name: str,
    zip_file_path: str,
) -> Tuple[Path, str]:
    """Copy a workspace-local zip into the archive registry."""
    from narranexus.platform.utils.workspace_paths import resolve_inside_workspace

    ws = _agent_workspace_root(agent_id, user_id)
    if ws is None:
        raise ValueError("Cannot resolve agent workspace")
    # Lexical workspace root, resolved candidate: `..`, an absolute path
    # elsewhere and a workspace dir swapped for a link to another tenant's
    # tree all fail here (2026-10-28).
    try:
        src = resolve_inside_workspace(ws, zip_file_path)
    except ValueError as e:
        raise ValueError(f"zip_file_path must be inside this agent's workspace ({e})") from None
    if not src.exists() or not src.is_file():
        raise ValueError(f"file not found: {src}")

    # Same admission rules as the upload route — `skill_archives` has two write
    # entry points and they used to disagree about what a valid archive is.
    # This one additionally demands SKILL.md: the caller hands us a path inside
    # its own workspace, so "you pointed at the wrong file" is the likely error
    # and is worth saying immediately. The route stays lenient by design (see
    # `_validate_skill_archive`).
    validate_skill_archive_path(src, require_skill_md=True)

    out_path = prepare_archive_target(user_id, skill_name)
    shutil.copy2(src, out_path)
    sha = file_sha256(out_path)
    logger.info(f"Local-zip archive registered for '{skill_name}': {out_path}")
    return out_path, sha


async def register_archive(
    user_id: str,
    skill_name: str,
    source_type: str,
    sha256: str,
    source_url: Optional[str] = None,
    archive_path: Optional[str] = None,
) -> None:
    db = await get_db_client()
    repo = SkillArchiveRepository(db)
    await repo.upsert(
        user_id=user_id,
        skill_name=skill_name,
        source_type=source_type,
        source_url=source_url,
        archive_path=archive_path,
        sha256=sha256,
    )


async def list_unbackedup(user_id: str, agent_id: str) -> List[str]:
    """Compare installed skills (filesystem) with archive registry; return skill names missing an archive."""
    db = await get_db_client()
    repo = SkillArchiveRepository(db)
    archives = await repo.list_for_user(user_id)
    archived = {a.skill_name for a in archives}

    ws = _agent_workspace_root(agent_id, user_id)
    if not ws:
        return []
    skills_dir = ws / "skills"
    if not skills_dir.is_dir():
        return []
    installed = [
        d.name for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".") and not _dir_is_builtin(d)
    ]
    return sorted([s for s in installed if s not in archived])


async def backup_after_api_install(
    user_id: str,
    skill_name: str,
    source_type: str,
    source_url: Optional[str],
    original_zip_path: Optional[Path] = None,
    branch: Optional[str] = "main",
    github_token: Optional[str] = None,
) -> Optional[str]:
    """Auto-archive immediately after the public install_skill API completes.
    Returns the resulting archive_path (str), or None if archiving was skipped.

    Archiving is best-effort: a failure here must not fail the install the user
    just did. But "best-effort" is not "silent" — see the handler at the bottom
    for which failures are expected noise and which are programming errors.
    """
    try:
        if source_type == "github" and source_url:
            archive_path, sha = await archive_github_tarball(
                user_id=user_id,
                skill_name=skill_name,
                github_url=source_url,
                branch=branch or "main",
                github_token=github_token,
            )
            await register_archive(
                user_id=user_id,
                skill_name=skill_name,
                source_type="github",
                source_url=source_url,
                archive_path=str(archive_path),
                sha256=sha,
            )
            return str(archive_path)
        if source_type == "zip" and original_zip_path and original_zip_path.exists():
            out = prepare_archive_target(user_id, skill_name)
            shutil.copy2(original_zip_path, out)
            sha = file_sha256(out)
            await register_archive(
                user_id=user_id,
                skill_name=skill_name,
                source_type="zip",
                source_url=None,
                archive_path=str(out),
                sha256=sha,
            )
            return str(out)
    except (ValueError, OSError, httpx.HTTPError) as e:
        # Expected, environmental, already-actionable-from-the-message failures:
        # bad skill name / GitHub URL (ValueError), disk or permission problems
        # and SameFileError (OSError), tarball download trouble (HTTPError).
        # These are the ones a warning line genuinely covers.
        logger.warning(f"backup_after_api_install failed for {skill_name}: {e}")
    except Exception:
        # Anything else is a bug in THIS code path, not an environment problem.
        # The previous blanket `except Exception` + warning is exactly how a
        # guaranteed `copy2(tgt, tgt)` -> SameFileError sat here unnoticed while
        # the `skill_archives` row was silently never written (see importer.py's
        # zip branch, fixed in the preceding commit). Log the traceback so the
        # next one is findable instead of being one grey line in the log.
        logger.exception(f"backup_after_api_install crashed for {skill_name}")
    return None
