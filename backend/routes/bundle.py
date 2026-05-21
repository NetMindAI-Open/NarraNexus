"""
@file_name: bundle.py
@author: NetMind.AI
@date: 2026-05-08
@description: REST API for .nxbundle export/import

Subproject 2 endpoints (under /api/bundle):
- POST /export                    Build a bundle and stream it back
- POST /import/preflight          Validate + diff against this instance
- POST /import/from-url           Fetch a bundle URL server-side, then preflight
- POST /import/confirm            Execute the import using a preflight token
- GET  /skills/archives           List skill archives for current user
"""

import io
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import StreamingResponse, FileResponse
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.bundle.builder import ExportSelection, build_bundle
from xyz_agent_context.bundle.importer import preflight, confirm
from xyz_agent_context.bundle.security import MAX_BUNDLE_BYTES, file_sha256
from xyz_agent_context.repository import SkillArchiveRepository
from backend.auth import resolve_current_user_id


router = APIRouter()


async def _user_id_for_request(request: Request) -> str:
    # Unified accessor — auth_middleware populates request.state.user_id
    # from JWT (cloud) or X-User-Id header (local) before this runs.
    return await resolve_current_user_id(request)


class SkillExportSpec(BaseModel):
    skill_name: str
    install_method: str  # url | zip | full_copy | builtin | skip
    # Per-agent attribution: each (agent_id, skill_dir) pair is independent.
    agent_id: Optional[str] = None
    # Filesystem dir name under the agent's skills/ dir. fs-unique per agent,
    # disambiguates two skills with the same SKILL.md `name` (frontmatter
    # name can duplicate; dir name cannot).
    skill_dir: Optional[str] = None
    source_url: Optional[str] = None
    source_type: Optional[str] = "github"
    branch: Optional[str] = "main"
    archive_path: Optional[str] = None
    manual_zip_path: Optional[str] = None


class ExportRequest(BaseModel):
    agent_ids: List[str]
    team_id: Optional[str] = None
    team_intro_md: Optional[str] = None
    skills: List[SkillExportSpec] = []
    social_entity_selection: Optional[Dict[str, List[str]]] = None
    workspace_excludes: Optional[Dict[str, List[str]]] = None
    include_chat_history: bool = True
    embedding_provider: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dim: Optional[int] = None
    # B6: explicit confirmation that sensitive files inside zip-archived skills
    # may be shipped (the user saw the modal and clicked through).
    accept_sensitive_zips: bool = False
    # B2: per-agent narrative allowlist; None = include all
    narrative_selection: Optional[Dict[str, List[str]]] = None
    # B2: per-narrative event allowlist; None = include all
    event_selection: Optional[Dict[str, List[str]]] = None
    # P7: per-agent job_id allowlist; None = include all (subject to
    # narrative cascade — see builder).
    job_selection: Optional[Dict[str, List[str]]] = None
    # Bus channel allowlist (channel_id list). None = ship every owner-owned
    # channel that has at least one closure agent as a member (default).
    # Selected channels still must satisfy ownership + ≥1 closure-agent member.
    bus_channel_selection: Optional[List[str]] = None
    # Per-agent MCP allowlist; None or {} = ship no MCP (opt-in by design —
    # MCP URLs frequently point at private services).
    mcp_selection: Optional[Dict[str, List[str]]] = None
    # Per-agent artifact allowlist; None = include all. Underlying files always
    # travel with workspace.tar.gz; this filters only the DB pointer rows.
    artifact_selection: Optional[Dict[str, List[str]]] = None


@router.post("/export")
async def export_bundle(payload: ExportRequest, request: Request):
    user_id = await _user_id_for_request(request)
    if not payload.agent_ids:
        raise HTTPException(status_code=400, detail="agent_ids is required")

    out_dir = Path(tempfile.mkdtemp(prefix="nx-export-"))
    fname = f"nxbundle-{int(__import__('time').time())}.nxbundle"
    out_path = out_dir / fname

    # skill_methods is now a list of per-(agent, skill) entries. The builder
    # iterates this list and packages each one independently — ships one
    # archive per (agent_id, skill_name) so each agent's `.skill_meta.json`
    # (env_config / study_result) is preserved independently under Full mode.
    skill_methods = [
        {
            "skill_name": s.skill_name,
            "agent_id": s.agent_id,
            "skill_dir": s.skill_dir,
            "install_method": s.install_method,
            "source_url": s.source_url,
            "source_type": s.source_type,
            "branch": s.branch,
            "archive_path": s.archive_path,
            "manual_zip_path": s.manual_zip_path,
        }
        for s in payload.skills
    ]

    selection = ExportSelection(
        agent_ids=payload.agent_ids,
        team_id=payload.team_id,
        team_intro_md=payload.team_intro_md or "",
        skill_methods=skill_methods,
        social_entity_selection=payload.social_entity_selection,
        workspace_excludes=payload.workspace_excludes,
        include_chat_history=payload.include_chat_history,
        embedding_provider=payload.embedding_provider,
        embedding_model=payload.embedding_model,
        embedding_dim=payload.embedding_dim,
        accept_sensitive_zips=payload.accept_sensitive_zips,
        narrative_selection=payload.narrative_selection,
        event_selection=payload.event_selection,
        job_selection=payload.job_selection,
        bus_channel_selection=payload.bus_channel_selection,
        mcp_selection=payload.mcp_selection,
        artifact_selection=payload.artifact_selection,
    )

    try:
        result = await build_bundle(user_id, selection, out_path)
    except Exception as e:
        shutil.rmtree(out_dir, ignore_errors=True)
        # B6: surface SensitiveZipDetected as 409 with structured payload
        # so the frontend can show a per-skill confirmation modal.
        from xyz_agent_context.bundle.builder import SensitiveZipDetected
        if isinstance(e, SensitiveZipDetected):
            raise HTTPException(
                status_code=409,
                detail={
                    "error_code": "SENSITIVE_FILES_IN_SKILL_ZIP",
                    "message": "Some zip-archived skills contain sensitive files. Confirm before export.",
                    "hits": e.hits,
                },
            )
        logger.exception("export build failed")
        raise HTTPException(status_code=500, detail=str(e))

    # Stream the file back; clean up on close.
    def iterfile():
        try:
            with open(out_path, "rb") as f:
                while True:
                    chunk = f.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    return StreamingResponse(
        iterfile(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{fname}"',
            "X-Bundle-Manifest-Sha256": result["manifest"].get("integrity_sha256", ""),
            "X-Bundle-Warnings-Count": str(len(result.get("warnings", []))),
            # Info = expected non-actionable events (e.g. external-edge drops).
            # Surfaced separately so the frontend doesn't alarm on them.
            "X-Bundle-Info-Count": str(len(result["manifest"].get("info", []))),
            "X-Bundle-External-Edges-Dropped": str(
                result["manifest"].get("info_counters", {}).get("skipped_external_edge", 0)
            ),
        },
    )


@router.post("/import/preflight")
async def import_preflight(file: UploadFile = File(...), request: Request = None):
    user_id = await _user_id_for_request(request)
    tmpdir = Path(tempfile.mkdtemp(prefix="nx-preflight-"))
    bundle_path = tmpdir / (file.filename or "upload.nxbundle")

    try:
        contents = await file.read()
        bundle_path.write_bytes(contents)
        result = await preflight(bundle_path, user_id)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("preflight failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # Bundle file copy is not needed beyond preflight (the extracted dir lives elsewhere)
        try:
            bundle_path.unlink(missing_ok=True)
            tmpdir.rmdir()
        except Exception:
            pass


class ConfirmRequest(BaseModel):
    preflight_token: str


@router.post("/import/confirm")
async def import_confirm(payload: ConfirmRequest, request: Request):
    user_id = await _user_id_for_request(request)
    try:
        return await confirm(payload.preflight_token, user_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("confirm failed")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# /import/from-url — "the website pointed me at a template, fetch it for me
# and start preflight"
# ---------------------------------------------------------------------------
#
# Why this exists (vs the browser downloading then POSTing to /preflight):
#  - one network hop (website → backend) instead of two (website → browser →
#    backend), and the browser never holds the bundle bytes
#  - the install button can be a one-liner deep link; the browser only ships
#    a URL string
#
# Security envelope (each control catches a different abuse):
#  - URL host allowlist  → SSRF defense (the classic Capital-One-style attack
#    where a public fetch endpoint is turned into a tunnel into internal
#    services / cloud metadata IPs)
#  - per-request size cap → bound disk usage
#  - per-request timeout  → bound request lifetime / connection-pool exhaustion
#  - optional sha256      → integrity + prevents the website serving a wrong
#    or tampered bundle from silently being installed
#  - no redirect-follow   → an upstream 302 to localhost would otherwise
#    sidestep the allowlist
#  - JWT/X-User-Id auth   → only logged-in users can trigger fetches

# Conservative defaults — production should override via env. Compatible with
# Phase 1 (single template host on narra.nexus); object storage / R2 hosts
# get added here when Phase 2+ moves files off public/.
_DEFAULT_ALLOWED_HOSTS_CLOUD = "narra.nexus,www.narra.nexus,website.narra.nexus"
# Local mode (sqlite, desktop DMG, bash run.sh) also needs to fetch from
# locally-served bundles — the website running on localhost:3001 during dev
# and a future local-marketplace flow. Production locks this back down via
# is_cloud_mode + the explicit env override.
_DEFAULT_ALLOWED_HOSTS_LOCAL = (
    "narra.nexus,www.narra.nexus,website.narra.nexus,localhost,127.0.0.1,[::1]"
)
_FETCH_TIMEOUT_SEC = 30.0
_FETCH_CHUNK_BYTES = 64 * 1024


def _allowed_fetch_hosts() -> set[str]:
    """Resolve allowed bundle-fetch hosts.

    Priority:
      1. `BUNDLE_FETCH_ALLOWED_HOSTS` env var (explicit override always wins —
         cloud ops can lock this to exactly the prod hosts they trust, dev can
         extend with object-storage or staging hosts)
      2. Local mode default — includes localhost / loopback so a DMG or
         `bash run.sh` user can install from a locally-served website bundle
      3. Cloud mode default — narra.nexus only, no loopback (loopback in cloud
         mode would be an SSRF foothold)
    """
    from xyz_agent_context.settings import settings  # late import — avoid cycle
    explicit = os.environ.get("BUNDLE_FETCH_ALLOWED_HOSTS", "").strip()
    if explicit:
        raw = explicit
    elif settings.is_cloud_mode:
        raw = _DEFAULT_ALLOWED_HOSTS_CLOUD
    else:
        raw = _DEFAULT_ALLOWED_HOSTS_LOCAL
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _host_allowed(host: Optional[str]) -> bool:
    if not host:
        return False
    return host.lower() in _allowed_fetch_hosts()


async def _stream_download(url: str, dst: Path) -> None:
    """Stream-fetch `url` into `dst`, enforcing size cap + timeout.
    Raises HTTPException on size overrun / upstream non-200 / network errors."""
    try:
        async with httpx.AsyncClient(
            timeout=_FETCH_TIMEOUT_SEC,
            follow_redirects=False,  # see security envelope note above
        ) as client:
            async with client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise HTTPException(
                        status_code=502,
                        detail=f"upstream returned HTTP {resp.status_code}",
                    )
                # Cheap Content-Length pre-check (servers can lie / omit,
                # so the streamed accumulator below is the authoritative cap).
                cl = resp.headers.get("content-length")
                if cl is not None:
                    try:
                        if int(cl) > MAX_BUNDLE_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"bundle declares size {cl}B exceeding cap {MAX_BUNDLE_BYTES}B",
                            )
                    except ValueError:
                        pass  # malformed header — fall through to streaming cap
                total = 0
                with open(dst, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=_FETCH_CHUNK_BYTES):
                        total += len(chunk)
                        if total > MAX_BUNDLE_BYTES:
                            raise HTTPException(
                                status_code=413,
                                detail=f"bundle exceeds cap {MAX_BUNDLE_BYTES}B",
                            )
                        f.write(chunk)
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="upstream fetch timed out")
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"upstream fetch failed: {e}")


class ImportFromUrlRequest(BaseModel):
    url: str
    # Optional — when set, the downloaded bundle's sha256 must match exactly.
    # Phase 1 callers (website install button) always include this; the API
    # leaves it optional for ad-hoc curl / future flows.
    expected_sha256: Optional[str] = None


@router.post("/import/from-url")
async def import_from_url(payload: ImportFromUrlRequest, request: Request):
    """Fetch a bundle from a URL on behalf of the caller, then run preflight.

    Returns the same response shape as POST /import/preflight, so the
    frontend can drop the result into the existing review UI.
    """
    user_id = await _user_id_for_request(request)

    # ── 1. URL validation ─────────────────────────────────────────────────
    parsed = urlparse(payload.url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=400,
            detail="unsupported URL scheme — only http(s) is allowed",
        )
    if not _host_allowed(parsed.hostname):
        raise HTTPException(
            status_code=403,
            detail=(
                f"URL host {parsed.hostname!r} is not in the bundle-fetch "
                "allowlist. Set BUNDLE_FETCH_ALLOWED_HOSTS to extend."
            ),
        )

    # ── 2. Fetch + (optional) sha256 verify + preflight ───────────────────
    tmpdir = Path(tempfile.mkdtemp(prefix="nx-from-url-"))
    bundle_path = tmpdir / "downloaded.nxbundle"
    try:
        await _stream_download(payload.url, bundle_path)

        if payload.expected_sha256:
            actual = file_sha256(bundle_path)
            if actual.lower() != payload.expected_sha256.lower():
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"sha256 mismatch — expected "
                        f"{payload.expected_sha256[:12]}…, got {actual[:12]}…"
                    ),
                )

        logger.info(
            "bundle from-url: user={} host={} size={}B",
            user_id,
            parsed.hostname,
            bundle_path.stat().st_size,
        )
        return await preflight(bundle_path, user_id)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("from-url import failed")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        # preflight has already copied what it needs into its own work_dir,
        # so the staged download file is no longer needed.
        try:
            bundle_path.unlink(missing_ok=True)
            tmpdir.rmdir()
        except Exception:
            pass


class BusChannelsPreviewRequest(BaseModel):
    agent_ids: List[str]


@router.post("/export/preview/bus-channels")
async def preview_bus_channels(payload: BusChannelsPreviewRequest, request: Request):
    """List the message-bus channels that *would* ship for the given agent
    closure under default rules (owner==current user AND ≥1 closure agent
    member). The frontend uses this to render a per-channel picker.
    """
    user_id = await _user_id_for_request(request)
    if not payload.agent_ids:
        return {"channels": []}

    db = await get_db_client()
    closure_set = set(payload.agent_ids)
    # bus_channels.created_by actually stores an AGENT_ID (the channel owner
    # agent), not a user_id — see local_bus.create_channel for the source
    # of truth. We chain it through agents.agent_id → agents.created_by to
    # find channels owned by an agent of the current user. (An earlier
    # query passed user_id directly to created_by and silently dropped
    # every agent-created channel from this preview.)
    owned_chs = await db.execute(
        """SELECT ch.*
           FROM bus_channels ch
           JOIN agents a ON ch.created_by = a.agent_id
           WHERE a.created_by = %s""",
        params=(user_id,),
        fetch=True,
    )
    out: List[Dict[str, Any]] = []
    for ch in owned_chs:
        cid = ch["channel_id"]
        members = await db.get("bus_channel_members", {"channel_id": cid})
        closure_member_ids = [m["agent_id"] for m in members if m["agent_id"] in closure_set]
        if not closure_member_ids:
            continue
        msgs = await db.get("bus_messages", {"channel_id": cid})
        msg_count = len(msgs)
        out.append({
            "channel_id": cid,
            "name": ch.get("name") or "",
            "channel_type": ch.get("channel_type") or "",
            "in_closure_member_ids": closure_member_ids,
            "all_member_ids": [m["agent_id"] for m in members],
            "message_count": msg_count,
            "created_at": ch.get("created_at"),
        })
    return {"channels": out}


class ArtifactsPreviewRequest(BaseModel):
    agent_ids: List[str]


@router.post("/export/preview/artifacts")
async def preview_artifacts(payload: ArtifactsPreviewRequest, request: Request):
    """Return ALL artifacts for each agent in the closure, grouped by agent_id.

    The wizard's Artifacts tab uses this to render the per-artifact picker
    (default = all selected). Same ownership pattern as the rest of the
    wizard: each agent must belong to the requesting user.
    """
    user_id = await _user_id_for_request(request)
    if not payload.agent_ids:
        return {"agents": {}}
    db = await get_db_client()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for aid in payload.agent_ids:
        agent_row = await db.get_one("agents", {"agent_id": aid})
        if not agent_row or agent_row.get("created_by") != user_id:
            # Silently skip non-owned agents. Wizard already filters at the
            # closure level, but this guards direct API misuse.
            continue
        rows = await db.get("instance_artifacts", {"agent_id": aid})
        out[aid] = [
            {
                "artifact_id": r.get("artifact_id"),
                "title": r.get("title"),
                "kind": r.get("kind"),
                "size_bytes": int(r.get("size_bytes") or 0),
                "pinned": bool(r.get("pinned")),
                "session_id": r.get("session_id"),
                "file_path": r.get("file_path"),
                "created_at": r.get("created_at"),
                "updated_at": r.get("updated_at"),
            }
            for r in rows
        ]
    return {"agents": out}


class McpsPreviewRequest(BaseModel):
    agent_ids: List[str]


@router.post("/export/preview/mcps")
async def preview_mcps(payload: McpsPreviewRequest, request: Request):
    """Return all MCP URLs registered against each closure agent, grouped by
    agent_id. The wizard's Skills & MCP tab uses this to render the MCP
    picker (default = none selected — MCP is opt-in)."""
    user_id = await _user_id_for_request(request)
    if not payload.agent_ids:
        return {"agents": {}}
    db = await get_db_client()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for aid in payload.agent_ids:
        agent_row = await db.get_one("agents", {"agent_id": aid})
        if not agent_row or agent_row.get("created_by") != user_id:
            continue
        rows = await db.get("mcp_urls", {"agent_id": aid, "user_id": user_id})
        out[aid] = [
            {
                "mcp_id": r.get("mcp_id"),
                "name": r.get("name"),
                "url": r.get("url"),
                "description": r.get("description"),
                "is_enabled": bool(r.get("is_enabled")),
                "connection_status": r.get("connection_status"),
            }
            for r in rows
        ]
    return {"agents": out}


@router.get("/skills/archives")
async def list_skill_archives(request: Request):
    """List the skill archives registered for the current user."""
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    repo = SkillArchiveRepository(db)
    items = await repo.list_for_user(user_id)
    return {"archives": [a.model_dump() for a in items]}


class UploadArchiveRequest(BaseModel):
    skill_name: str
    source_type: str  # "zip" or "github"
    source_url: Optional[str] = None


@router.post("/skills/archives/upload")
async def upload_archive(
    request: Request,
    skill_name: str = Form(...),
    source_type: str = Form(...),
    source_url: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
):
    """Manual archive upload: user provides a zip (or a GitHub URL) for a skill that's missing an archive."""
    user_id = await _user_id_for_request(request)
    db = await get_db_client()
    repo = SkillArchiveRepository(db)

    archives_dir = Path.home() / ".nexusagent" / "skill_archives" / user_id
    archives_dir.mkdir(parents=True, exist_ok=True)

    if source_type == "github":
        if not source_url:
            raise HTTPException(status_code=400, detail="source_url required for github")
        # Defer actual tarball download to lazy time; record source.
        await repo.upsert(
            user_id=user_id,
            skill_name=skill_name,
            source_type="github",
            source_url=source_url,
            archive_path=None,
            sha256="pending",
        )
        return {"success": True, "skill_name": skill_name, "source_type": "github"}

    if source_type == "zip":
        if not file:
            raise HTTPException(status_code=400, detail="file required for zip")
        target = archives_dir / f"{skill_name}.zip"
        contents = await file.read()
        target.write_bytes(contents)
        from xyz_agent_context.bundle.security import bytes_sha256
        sha = bytes_sha256(contents)
        await repo.upsert(
            user_id=user_id,
            skill_name=skill_name,
            source_type="zip",
            source_url=None,
            archive_path=str(target),
            sha256=sha,
        )
        return {"success": True, "skill_name": skill_name, "source_type": "zip", "sha256": sha}

    raise HTTPException(status_code=400, detail="source_type must be 'github' or 'zip'")
