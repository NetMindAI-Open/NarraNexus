"""
@file_name: skill_backup.py
@author: NetMind.AI
@date: 2026-05-08
@description: Skill archive helpers — used by MCP tools and the install API

Subproject 2 §8.12.2 ~ §8.12.5.
"""

import io
import shutil
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

import httpx
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.repository import SkillArchiveRepository
from .security import bytes_sha256, file_sha256


SKILL_ARCHIVES_ROOT = Path.home() / ".nexusagent" / "skill_archives"
# SINGLE-WORKER ASSUMPTION: archive_path columns are absolute local fs paths.
# Multi-pod scale needs shared volume or object-store URLs — see
# .mindflow/project/references/scaling_assumptions.md §2.


def _user_archive_dir(user_id: str) -> Path:
    d = SKILL_ARCHIVES_ROOT / user_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _agent_workspace_root(agent_id: str, user_id: str) -> Optional[Path]:
    """Resolve canonical workspace dir; fall back to legacy `_user_` infix."""
    from xyz_agent_context.settings import settings as core_settings
    base = Path(core_settings.base_working_path)
    candidates = [
        base / f"{agent_id}_{user_id}",            # canonical
        base / f"{agent_id}_user_{user_id}",       # legacy
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
    archive_dir = _user_archive_dir(user_id)
    out_path = archive_dir / f"{skill_name}.tar.gz"
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
            raise ValueError(
                f"Failed to download tarball: HTTP {resp.status_code} ({tarball_url})"
            )
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
    archive_dir = _user_archive_dir(user_id)
    out_path = archive_dir / f"{skill_name}.zip"
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
    src = Path(zip_file_path)
    if not src.is_absolute():
        ws = _agent_workspace_root(agent_id, user_id)
        if not ws:
            raise ValueError("Cannot resolve agent workspace")
        src = (ws / src).resolve()
    src = src.resolve()

    ws = _agent_workspace_root(agent_id, user_id)
    if ws is None:
        raise ValueError("Cannot resolve agent workspace")
    ws_resolved = ws.resolve()
    # Use Path.is_relative_to (3.9+) for robust prefix check; falls back
    # to a string-based check on older Pythons. Defense-in-depth against
    # `/foo/agent_a_user_x/../../etc/passwd` style attempts that resolve
    # to outside the workspace.
    try:
        is_in_ws = src.is_relative_to(ws_resolved)
    except AttributeError:  # pragma: no cover (Python < 3.9)
        is_in_ws = str(src).startswith(str(ws_resolved) + "/") or str(src) == str(ws_resolved)
    if not is_in_ws:
        raise ValueError("zip_file_path must be inside this agent's workspace")
    if not src.exists() or not src.is_file():
        raise ValueError(f"file not found: {src}")

    # Verify SKILL.md inside
    try:
        with zipfile.ZipFile(src, "r") as zf:
            names = [n for n in zf.namelist() if n.lower().endswith("skill.md")]
            if not names:
                raise ValueError("zip does not contain SKILL.md")
    except zipfile.BadZipFile:
        raise ValueError("Not a valid zip file")

    archive_dir = _user_archive_dir(user_id)
    out_path = archive_dir / f"{skill_name}.zip"
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
    installed = [d.name for d in skills_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
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
    Returns the resulting archive_path (str), or None if archiving was skipped."""
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
                user_id=user_id, skill_name=skill_name, source_type="github",
                source_url=source_url, archive_path=str(archive_path), sha256=sha,
            )
            return str(archive_path)
        if source_type == "zip" and original_zip_path and original_zip_path.exists():
            archive_dir = _user_archive_dir(user_id)
            out = archive_dir / f"{skill_name}.zip"
            shutil.copy2(original_zip_path, out)
            sha = file_sha256(out)
            await register_archive(
                user_id=user_id, skill_name=skill_name, source_type="zip",
                source_url=None, archive_path=str(out), sha256=sha,
            )
            return str(out)
    except Exception as e:
        logger.warning(f"backup_after_api_install failed for {skill_name}: {e}")
    return None
