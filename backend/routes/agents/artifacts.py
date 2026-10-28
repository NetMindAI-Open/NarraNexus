"""
@file_name: artifacts.py
@author: Bin Liang
@date: 2026-05-08
@description: JWT-authed REST endpoints for agent-emitted Artifact tabs (pointer model).

Endpoints:
- GET    /{agent_id}/artifacts                       list (scope=session|pinned, session_id?)
- POST   /{agent_id}/artifacts/register              manual register: register a workspace file as an artifact
- POST   /{agent_id}/artifacts/{aid}/heal            try to recover a broken pointer (file_path NULL or off-disk)
- GET    /{agent_id}/artifacts/{aid}                 metadata
- GET    /{agent_id}/artifacts/{aid}/view-token      mint a short-TTL HMAC token for the public raw route
- PATCH  /{agent_id}/artifacts/{aid}                 { pinned?, title? }
- DELETE /{agent_id}/artifacts/{aid}                 remove DB row (workspace files are NOT touched)

This router is a thin HTTP shell: business logic (registration validation,
heal recovery strategy) lives in `narranexus.platform.artifact.ArtifactService`;
plain CRUD goes through `ArtifactRepository`. Handlers only do auth, HTTP
mapping, and response shaping.

The raw-content route lives in `artifacts_public.py` under `/api/public/artifacts/raw/{token}/`
(JWT-bypassed; the HMAC token IS the auth). That keeps multi-file HTML
artifacts loadable via iframe `src=` in cloud mode.

Deletion is registry-only by design (2026-05-14-r3): removing an artifact
tab never deletes the agent's workspace files. The user can clean those up
via the workspace section in the config panel.
"""

from __future__ import annotations

import os
import re
import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile
from loguru import logger
from pydantic import BaseModel, Field

from narranexus.platform.artifact import (
    MAX_ARTIFACT_BYTES,
    ArtifactEditConflict,
    ArtifactError,
    ArtifactService,
    ArtifactPathEscape,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.settings import settings
from narranexus.platform.schema import Artifact, EmbedMode, HealResult
from narranexus.platform.utils.db.db_factory import get_db_client

from backend.middleware.body_size import MAX_OFFICE_ASSET_BYTES, PUT_CONTENT_MARGIN
from backend.routes.artifacts import _token as _artifact_token


router = APIRouter()


async def _verify_agent_ownership(request: Request, agent_id: str) -> None:
    """Verify that the JWT user owns the agent. Raises HTTPException(403) on failure.

    In local mode (no JWT / no request.state.user_id), ownership is not enforced.
    In cloud mode, the agent's created_by must match the JWT user_id.
    """
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return  # Local mode — no auth enforcement
    user_id = request.state.user_id
    db = await get_db_client()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent:
        raise HTTPException(404, "agent not found")
    if agent.get("created_by") != user_id:
        raise HTTPException(403, "permission denied: you do not own this agent")


async def _resolve_agent_user_id(agent_id: str) -> str:
    """Look up an agent's owner user_id (the workspace owner).

    Used by the manual-register and heal endpoints to resolve the workspace
    path `{base}/{agent_id}_{user_id}/` exactly the way the agent loop and MCP
    tool do (the agent_runtime overrides ctx.user_id with agent.created_by).
    """
    db = await get_db_client()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent or not agent.get("created_by"):
        raise HTTPException(404, "agent not found")
    return str(agent["created_by"])


async def _get_owned_artifact(repo: ArtifactRepository, agent_id: str, artifact_id: str) -> Artifact:
    """Fetch an artifact and enforce it belongs to `agent_id`; 404 otherwise
    (404, not 403, so probing cannot map which artifact_ids exist)."""
    art = await repo.get_by_id(artifact_id)
    if art is None or art.agent_id != agent_id:
        raise HTTPException(404, "artifact not found")
    return art


class PatchArtifact(BaseModel):
    pinned: Optional[bool] = None
    title: Optional[str] = None


class PutContentRequest(BaseModel):
    """A user edit from an editing surface: the FULL new file content plus the
    sha256 the editor's copy was based on (optimistic lock)."""

    content: str
    base_hash: str


class RegisterRequest(BaseModel):
    file_path: str = Field(..., description="Workspace-relative or absolute path to the entry file")
    kind: str
    title: str
    description: Optional[str] = None
    target_artifact_id: Optional[str] = None


class HealRequest(BaseModel):
    """Optional entry_path lets the user pick a candidate the auto-heuristic
    surfaced but couldn't pick on its own. Omitted → server runs the
    heuristic and registers automatically if it finds a unique match."""

    entry_path: Optional[str] = None


class ViewTokenResponse(BaseModel):
    token: str
    raw_url: str
    expires_at: int


class OpenUrlRequest(BaseModel):
    url: str
    title: Optional[str] = None


class EmbedModeRequest(BaseModel):
    # None clears the user's override, reverting to the probe recommendation.
    # Typed as EmbedMode so pydantic rejects unknown values (422) — single
    # source of truth with the schema, and adding a third mode later just works.
    mode: Optional[EmbedMode] = None


# ── list / register ──────────────────────────────────────────────────────────


@router.get("/{agent_id}/artifacts", response_model=List[Artifact])
async def list_artifacts(
    request: Request,
    agent_id: str,
    scope: Literal["session", "pinned", "context"] = Query("session"),
    session_id: Optional[str] = Query(None),
):
    """
    List artifacts for an agent.

    Args:
        scope: 'session' (default) returns non-pinned artifacts for the given
               session_id; 'pinned' returns all pinned artifacts for the agent;
               'context' returns the agent's full awareness surface — own
               pinned ∪ every team it belongs to (list_for_agent_context),
               the same union the agent's state block draws from. This is the
               frontend's full-pull on open/switch/reconnect (spec
               artifact-events §3.3), so what the panel shows and what the
               agent believes exists can never disagree.
        session_id: Required when scope='session'.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    if scope == "context":
        return await repo.list_for_agent_context(agent_id)
    if scope == "pinned":
        return await repo.list_pinned(agent_id)
    if not session_id:
        raise HTTPException(400, "session_id is required when scope=session")
    return await repo.list_by_session(agent_id, session_id)


@router.post("/{agent_id}/artifacts/register", response_model=Artifact)
async def register_artifact(request: Request, agent_id: str, body: RegisterRequest):
    """
    Manually register a workspace file as an artifact.

    Powers the "register as artifact" action in the workspace tree viewer.
    Delegates to the same `ArtifactService.register` the MCP tool uses, so
    validation and path-confinement rules are identical.

    `file_path` may be absolute or workspace-relative; it must be inside the
    agent's workspace. Its directory becomes the artifact root (workspace-root
    entries serve as single-file artifacts).
    """
    await _verify_agent_ownership(request, agent_id)
    user_id = await _resolve_agent_user_id(agent_id)

    db = await get_db_client()
    service = ArtifactService(db)
    try:
        result = await service.register(
            agent_id=agent_id,
            user_id=user_id,
            session_id=None,  # manual registrations are always agent-scoped
            kind=body.kind,  # type: ignore[arg-type]
            entry_path=body.file_path,
            title=body.title,
            description=body.description,
            target_artifact_id=body.target_artifact_id,
        )
    except ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))

    repo = ArtifactRepository(db)
    art = await repo.get_by_id(result.artifact_id)
    if art is None:
        # Should be impossible — the service just wrote the row.
        raise HTTPException(500, "artifact disappeared after registration")
    return art


# ── URL tabs ─────────────────────────────────────────────────────────────────


@router.post("/{agent_id}/artifacts/url", response_model=Artifact)
async def open_url_artifact(request: Request, agent_id: str, body: OpenUrlRequest):
    """Open a web page as a URL-tab artifact.

    Server-side probes the URL's embeddability and stores the verdict; the
    frontend renders it as an iframe or a fallback card accordingly. The
    initial URL is SSRF-gated — a non-public target is rejected 400.
    """
    await _verify_agent_ownership(request, agent_id)
    user_id = await _resolve_agent_user_id(agent_id)

    # The browser-visible origin of the app, derived from the request headers
    # (same helper the raw route uses for its CSP). Passed to the self-origin
    # guard so it holds even if settings.public_base_url is unset/misconfigured.
    from backend.routes.artifacts.public import _app_origin
    app_origin = _app_origin(request)

    db = await get_db_client()
    service = ArtifactService(db)
    try:
        result = await service.open_url(
            agent_id=agent_id,
            user_id=user_id,
            session_id=None,  # URL tabs are agent-scoped like manual registers
            url=body.url,
            title=body.title,
            app_origin=app_origin,
        )
    except ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))

    repo = ArtifactRepository(db)
    art = await repo.get_by_id(result.artifact_id)
    if art is None:
        raise HTTPException(500, "artifact disappeared after registration")
    return art


@router.post("/{agent_id}/artifacts/{artifact_id}/embed-mode", response_model=Artifact)
async def set_embed_mode(request: Request, agent_id: str, artifact_id: str, body: EmbedModeRequest):
    """Set (or clear, mode=null) the user's manual embed override on a URL tab.

    The override wins over the probe recommendation for that tab. Rewrites the
    tab's on-disk doc; 404 if the artifact is missing / not owned / not a URL
    tab; 422 (pydantic) for an invalid mode value.
    """
    await _verify_agent_ownership(request, agent_id)

    db = await get_db_client()
    service = ArtifactService(db)
    try:
        return await service.set_embed_mode(
            agent_id=agent_id,
            artifact_id=artifact_id,
            mode=body.mode,
        )
    except ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))


# ── heal: recover a broken pointer ───────────────────────────────────────────


@router.post("/{agent_id}/artifacts/{artifact_id}/heal", response_model=HealResult)
async def heal_artifact(
    request: Request,
    agent_id: str,
    artifact_id: str,
    body: HealRequest,
):
    """Try to recover an artifact whose pointer is broken.

    A pointer is "broken" when the artifact row's `file_path` is empty/None,
    or when the on-disk entry file at that path no longer exists. The /raw/
    route returns 410 in either case, surfacing as a broken tab on the
    frontend.

    The recovery strategy (pointer re-check → user-picked path → workspace
    scan) lives in `ArtifactService.heal`; this handler only maps auth and
    errors.
    """
    await _verify_agent_ownership(request, agent_id)
    user_id = await _resolve_agent_user_id(agent_id)

    db = await get_db_client()
    service = ArtifactService(db)
    try:
        return await service.heal(
            agent_id=agent_id,
            user_id=user_id,
            artifact_id=artifact_id,
            entry_path=body.entry_path,
        )
    except ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))


# ── detail / view-token / patch / delete ─────────────────────────────────────


@router.get("/{agent_id}/artifacts/{artifact_id}/view-token", response_model=ViewTokenResponse)
async def mint_view_token(request: Request, agent_id: str, artifact_id: str):
    """
    Mint a short-TTL HMAC view token for an artifact's raw content.

    The frontend calls this once before loading the iframe `src` for HTML
    artifacts (and uses the same flow for non-HTML kinds for code symmetry —
    no JWT header needed on the raw fetch).

    The returned `raw_url` is the directory-style URL `/raw/{token}/`; the
    entry file is served at that URL, sibling assets at `/raw/{token}/{name}`.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    await _get_owned_artifact(repo, agent_id, artifact_id)

    token = _artifact_token.mint(agent_id=agent_id, artifact_id=artifact_id)
    # Decode exp without re-verifying — the payload is the b64url part before '.'.
    import base64
    import json as _json

    payload_b64 = token.split(".", 1)[0]
    pad = "=" * (-len(payload_b64) % 4)
    payload = _json.loads(base64.urlsafe_b64decode(payload_b64 + pad).decode("utf-8"))
    raw_url = f"/api/public/artifacts/raw/{token}/"
    return ViewTokenResponse(token=token, raw_url=raw_url, expires_at=int(payload["exp"]))


@router.get("/{agent_id}/artifacts/{artifact_id}", response_model=Artifact)
async def get_artifact(request: Request, agent_id: str, artifact_id: str):
    """Get artifact metadata."""
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    return await _get_owned_artifact(repo, agent_id, artifact_id)


@router.put("/{agent_id}/artifacts/{artifact_id}/content", response_model=Artifact)
async def put_artifact_content(request: Request, agent_id: str, artifact_id: str):
    """
    Persist a user edit onto the artifact's entry file (spec A §3.1).

    NO Pydantic body field, deliberately (review #334 r3 I1): FastAPI reads
    and parses any declared body BEFORE dependencies run, so a body field
    would turn every size gate into an after-the-fact check. The body is
    read STREAMED here with an accumulation cap; the declared-length fast
    reject lives in backend/middleware/body_size.py (the only layer that
    truly runs first). Adding a body field back re-opens the unbounded
    buffering hole.

    The service commits it as one atomic edit: optimistic lock against the
    on-disk content, temp-file + atomic-rename write, hash/size/updated_at
    refresh, history action="user_edited", staged "updated" event.

    409 carries {current_hash} in the detail so the editor can offer the
    re-base choice ("overwrite anyway" resends with it; "discard mine"
    reloads). Session-authed only — view tokens stay read-only.

    Request body: {"content": str, "base_hash": str} — hand-parsed, so this
    endpoint has no OpenAPI requestBody schema and its 422 detail is a plain
    string, not FastAPI's [{loc, msg, type}] shape.
    """
    await _verify_agent_ownership(request, agent_id)
    cap = MAX_ARTIFACT_BYTES + PUT_CONTENT_MARGIN
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > cap:
            raise HTTPException(413, "content too large")
        chunks.append(chunk)
    try:
        parsed = PutContentRequest.model_validate_json(b"".join(chunks))
    except ValueError:
        raise HTTPException(422, "body must be JSON with content and base_hash")
    db = await get_db_client()
    service = ArtifactService(db)
    try:
        return await service.save_user_content(
            agent_id=agent_id,
            artifact_id=artifact_id,
            content=parsed.content,
            base_hash=parsed.base_hash,
        )
    except ArtifactEditConflict as e:
        # Structured detail, not a plain string: current_hash is data the
        # editor needs, not a message for a human.
        raise HTTPException(
            status_code=e.code,
            detail={"error": str(e), "current_hash": e.current_hash},
        )
    except ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))


@router.post("/{agent_id}/artifacts/{artifact_id}/office-edit-commit", response_model=Artifact)
async def office_edit_commit(request: Request, agent_id: str, artifact_id: str):
    """
    Commit point for an office watch-page user edit (spec B §3.2).

    The bytes were already written by the officecli watch server (single
    resident writer); this refreshes the registry — hash/size/updated_at,
    history action="user_edited", staged event — so the agent's state block
    and other open surfaces learn about the change. Idempotent on an
    unchanged hash.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    service = ArtifactService(db)
    try:
        return await service.commit_office_user_edit(
            agent_id=agent_id, artifact_id=artifact_id
        )
    except ArtifactError as e:
        raise HTTPException(status_code=e.code, detail=str(e))


@router.post("/{agent_id}/artifacts/{artifact_id}/office-asset")
async def upload_office_asset(
    request: Request, agent_id: str, artifact_id: str, file: UploadFile
):
    """
    Land an asset (an image for a T2 replace) next to an office artifact's
    entry file and return its absolute server path — the watch server resolves
    `set … src=<path>` against ITS filesystem, which is the same one the
    entry lives on.

    The filename is reduced to a sanitized basename with a random prefix: the
    upload can never escape the entry's directory or overwrite the document.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    art = await _get_owned_artifact(repo, agent_id, artifact_id)
    if art.kind != "application/vnd.officecli-live":
        raise HTTPException(400, "office-asset only applies to office artifacts")
    if not art.file_path:
        raise HTTPException(410, "artifact has no entry file")

    try:
        entry_abs = ArtifactService(db).resolve_owned_entry(art)
    except ArtifactPathEscape:
        raise HTTPException(404, "artifact not found")
    entry_dir = os.path.dirname(entry_abs)
    if not os.path.isdir(entry_dir):
        raise HTTPException(410, "entry directory is gone")

    # Honest bounds (review #334 r3 I1): with an UploadFile in the
    # signature the framework has ALREADY consumed the multipart stream
    # before this line — python-multipart spools big bodies to disk, so the
    # real pre-buffer bound is the declared-length middleware
    # (backend/middleware/body_size.py, with a multipart framing margin)
    # plus the container's disk. The streamed-copy check below bounds the
    # FILE bytes we copy out of the spool; it is not, and cannot be, a
    # memory gate. No declared-length check here (review #334 r5 M4): the
    # header covers the whole multipart body incl. framing, so comparing it
    # to the single-file cap 413s a just-under-10MB image the streamed
    # check would rightly accept — the middleware owns the declared gate.

    # Unicode-category sanitize (review #334 I15): \w keeps letters/digits of
    # EVERY script (kana, hangul, cyrillic, arabic, CJK...), not a
    # we-only-thought-of-Chinese whitelist; separators/control chars become _.
    # Length-capped — a 500-char name would ENAMETOOLONG as an opaque 500.
    raw_name = os.path.basename(file.filename or "asset")
    safe = re.sub(r"[^\w.\-]", "_", raw_name, flags=re.UNICODE).lstrip(".") or "asset"
    # Truncate stem and extension SEPARATELY (review #334 r2 M2): a plain
    # [:120] on a long name eats the extension, and the raw route serves by
    # extension — a suffixless image "replaces successfully" and renders
    # nothing. lstrip(".") above already handled leading-dot names.
    stem, ext = os.path.splitext(safe)
    safe = stem[:104] + ext[:16]
    dest = os.path.join(entry_dir, f"{uuid.uuid4().hex[:8]}_{safe}")
    received = 0
    try:
        with open(dest, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                received += len(chunk)
                if received > MAX_OFFICE_ASSET_BYTES:
                    raise HTTPException(
                        413,
                        "asset too large "
                        f"({MAX_OFFICE_ASSET_BYTES // (1024 * 1024)} MB max)",
                    )
                out.write(chunk)
    except BaseException:
        # Never leave a partial file: the entry dir is served by the raw
        # route and counted by _dir_size.
        try:
            os.unlink(dest)
        except OSError:
            pass
        raise
    return {"path": dest}


@router.patch("/{agent_id}/artifacts/{artifact_id}", response_model=Artifact)
async def patch_artifact(request: Request, agent_id: str, artifact_id: str, body: PatchArtifact):
    """
    Update artifact metadata (pinned flag and/or title).

    Pinning an artifact clears its session_id, making it agent-scoped.
    Unpinning an agent-created artifact (no session to restore) is rejected
    with 400 — the caller should DELETE instead.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    existing = await _get_owned_artifact(repo, agent_id, artifact_id)

    if body.pinned is not None:
        if body.pinned is False and existing.original_session_id is None and existing.pinned:
            # Unpinning would restore a NULL session_id — the artifact would
            # become invisible (not in any session list, not in pinned list).
            # This is the case for agent-created artifacts. Force DELETE.
            raise HTTPException(
                400,
                "this artifact is agent-scoped (no session to restore); use DELETE to remove it instead of unpinning",
            )
        await repo.set_pinned(artifact_id, pinned=body.pinned)
    if body.title is not None:
        await repo.update_title(artifact_id, body.title)

    return await repo.get_by_id(artifact_id)


@router.delete("/{agent_id}/artifacts/{artifact_id}")
async def delete_artifact(request: Request, agent_id: str, artifact_id: str):
    """Delete an artifact's DB row.

    Registry-only: the agent's workspace files are NEVER touched. The user
    cleans those up via the workspace section in the config panel when they
    want to free disk. Keeping this surgical removed a class of footguns
    (workspace-root rmtree, shared-directory collisions) — see the 2026-05-14
    architecture decision recorded in the artifact-pointer-model spec.
    """
    await _verify_agent_ownership(request, agent_id)
    db = await get_db_client()
    repo = ArtifactRepository(db)
    await _get_owned_artifact(repo, agent_id, artifact_id)

    await repo.delete(artifact_id)
    logger.info(f"Artifact registry row deleted: {artifact_id}")
    return {"deleted": artifact_id}
