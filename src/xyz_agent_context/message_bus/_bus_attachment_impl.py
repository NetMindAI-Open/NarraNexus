"""
@file_name: _bus_attachment_impl.py
@author: NarraNexus
@date: 2026-07-20
@description: Staging + marker synthesis for files attached to bus messages.

The message bus carries multimodal content by REFERENCE, never by embedding
bytes. When an agent attaches a file to a bus message it names a handle:

  * an attachment ``file_id`` (``att_...``) — a file it received earlier
    (user upload / another agent's bus message), or
  * a workspace-relative path — a file it produced itself (an artifact,
    a report it just wrote).

On send we resolve the handle to a concrete source file inside the SENDER's
own workspace, then stage the bytes into the OWNER's per-user shared area
(``{base}/{user_id}/_shared/bus_files/{date}/``) with ``os.link`` (hard-link,
zero-copy on the same filesystem; copy fallback across devices). The bus
message stores only a small dict per file (metadata + a base-relative path).

Why the shared area works for delivery without a per-recipient copy: the bus
forbids cross-user messaging, and the per-user Executor bind-mounts the whole
``{base}/{user_id}`` subtree — so every same-user recipient can ``Read`` the
staged path in both local and cloud mode. See ``utils/workspace_paths.py``.

Security: ``sender_agent_id`` / ``owner_user_id`` come from authenticated
runtime context, never from the LLM. Workspace-relative handles are resolved
and checked to stay inside the sender's workspace (no ``../`` escape).
"""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from loguru import logger

from xyz_agent_context.schema.attachment_schema import derive_category_from_mime
from xyz_agent_context.utils.attachment_storage import (
    generate_file_id,
    is_valid_file_id,
    resolve_attachment_path,
)
from xyz_agent_context.utils.file_safety import ensure_within_directory
from xyz_agent_context.utils.workspace_paths import (
    agent_workspace_path,
    bus_files_dir,
    team_shared_dir,
)


def _base(base: Optional[str]) -> str:
    if base is None:
        from xyz_agent_context.settings import settings

        return settings.base_working_path
    return base


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _sniff_mime(name: str) -> str:
    """Best-effort MIME from a filename. The agent's built-in Read re-validates
    content at read time, so a coarse extension-based guess is sufficient here."""
    mime, _ = mimetypes.guess_type(name)
    return mime or "application/octet-stream"


def _link_or_copy(src: Path, dst: Path) -> None:
    """Hard-link ``src`` → ``dst``; fall back to a byte copy across devices.

    Hard-linking keeps large files from doubling on disk when an agent forwards
    or shares a file. ``os.link`` fails with ``EXDEV`` across filesystems (and a
    few other cases); any OSError falls back to ``shutil.copy2``.
    """
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def _resolve_ref_to_source(ref: str, sender_agent_id: str, owner_user_id: str, base: str) -> Optional[Path]:
    """Resolve one send-time handle to an absolute source file, or None.

    - ``att_...`` → an attachment in the sender's ``user_upload_files`` store.
    - anything else → a path relative to the sender's workspace, validated to
      stay inside that workspace (rejects ``../`` escapes and absolute paths).
    """
    ref = (ref or "").strip()
    if not ref:
        return None

    if is_valid_file_id(ref):
        return resolve_attachment_path(sender_agent_id, owner_user_id, ref)

    workspace = agent_workspace_path(sender_agent_id, owner_user_id, base=base).resolve()
    candidate = (workspace / ref).resolve()
    if not candidate.is_relative_to(workspace):
        logger.warning(f"[bus attachment] rejected workspace-escaping ref: {ref!r}")
        return None
    if not candidate.is_file():
        logger.warning(f"[bus attachment] ref not found: {ref!r}")
        return None
    return candidate


def _base_root(base: str) -> Path:
    return Path(base)


def _bus_att_dict(target: Path, base: str, *, original_name: str, mime: str) -> dict:
    """Build the bus-attachment dict for a file already written to ``target``.

    ``target`` is symlink-resolved (``ensure_within_directory`` calls
    ``.resolve()``), so the base MUST be resolved too before ``relpath`` — else a
    symlinked base (e.g. macOS ``/var`` → ``/private/var``) yields a ``../``-laden
    path that escapes and breaks both the marker and the shared-file resolver.
    """
    rel = os.path.relpath(target, _base_root(base).resolve())
    return {
        "file_id": target.stem,
        "original_name": original_name or target.name,
        "mime_type": mime,
        "size_bytes": target.stat().st_size,
        "category": derive_category_from_mime(mime).value,
        "rel_path": Path(rel).as_posix(),
    }


def _new_target(dest_dir: Path, suffix: str) -> Path:
    """Fresh ``{file_id}{suffix}`` path inside ``dest_dir`` (dir created)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    file_id = generate_file_id()
    on_disk_name = f"{file_id}{suffix.lower()}" if suffix else file_id
    return ensure_within_directory(dest_dir, on_disk_name, label="bus attachment")


async def _stage_into(src: Path, dest_dir: Path, base: str, *, original_name: str) -> dict:
    """Hard-link/copy ``src`` into ``dest_dir`` under a fresh file_id, returning
    the bus-attachment dict (metadata + base-relative staged path).

    The disk write runs in a thread: the hard-link path is instant, but the
    cross-device ``shutil.copy2`` fallback can move tens of MB and must not
    block the event loop the caller (route / MCP tool) runs on.
    """
    target = _new_target(dest_dir, src.suffix)
    await asyncio.to_thread(_link_or_copy, src, target)
    return _bus_att_dict(target, base, original_name=original_name, mime=_sniff_mime(original_name or src.name))


async def store_bytes_into_bus(
    *,
    user_id: str,
    raw_bytes: bytes,
    original_name: str,
    mime_type: str,
    base: Optional[str] = None,
) -> dict:
    """Write user-uploaded bytes into the per-user shared bus area and return
    the bus-attachment dict — the upload counterpart to ``resolve_and_stage_refs``.

    Used by the team-chat upload endpoint: a human attaches a file to a team
    message, so there is no source workspace file to reference — we persist the
    raw bytes directly under ``{base}/{user_id}/_shared/bus_files/{date}/``.
    Callers pass an authenticated ``user_id`` and a server-sniffed ``mime_type``.
    The write runs in a thread — uploads go up to ``MAX_UPLOAD_BYTES`` (50 MB
    default) and must not block the route's event loop.
    """
    base = _base(base)
    dest_dir = bus_files_dir(user_id, base) / _today_str()
    suffix = Path(original_name or "").suffix
    target = _new_target(dest_dir, suffix)
    await asyncio.to_thread(target.write_bytes, raw_bytes)
    return _bus_att_dict(target, base, original_name=original_name or target.name, mime=mime_type)


def _meta_sidecar_path(staged: Path) -> Path:
    """Sidecar path for a staged file's server-authoritative attachment dict.

    ``{file_id}_meta.json`` next to the staged file. The underscore (not a
    dot) keeps it invisible to ``resolve_shared_file_by_id``'s
    ``{file_id}.*`` glob, so the sidecar can never shadow the original.
    """
    return staged.parent / f"{staged.stem}_meta.json"


def store_bus_attachment_meta(
    user_id: str, attachment: dict, base: Optional[str] = None
) -> None:
    """Persist the server-built attachment dict next to its staged file.

    Written by the upload endpoint AFTER it finishes building the dict
    (including a Whisper ``transcript``). At send time the client echoes
    attachment dicts back over the wire; the server re-reads this sidecar
    instead of trusting any echoed field — see ``load_bus_attachment_meta``.
    Best-effort: a sidecar write failure must not fail the upload.
    """
    staged = resolve_shared_file_for_user(user_id, attachment.get("rel_path") or "", base)
    if staged is None:
        return
    try:
        _meta_sidecar_path(staged).write_text(
            json.dumps(attachment, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as e:
        logger.warning(f"[bus attachment] failed to write meta sidecar: {e}")


def load_bus_attachment_meta(
    user_id: str, rel_path: str, base: Optional[str] = None
) -> Optional[dict]:
    """Load the server-authoritative attachment dict for a staged file.

    Returns None when the rel_path fails the shared-area gate or no sidecar
    exists (e.g. agent-staged files, which never pass through the upload
    endpoint). Callers fall back to rebuilding minimal metadata from disk.
    """
    staged = resolve_shared_file_for_user(user_id, rel_path, base)
    if staged is None:
        return None
    sidecar = _meta_sidecar_path(staged)
    try:
        loaded = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None


async def resolve_and_stage_refs(
    *,
    sender_agent_id: str,
    owner_user_id: str,
    refs: List[str],
    base: Optional[str] = None,
) -> List[dict]:
    """Resolve send-time file handles and stage them into the shared bus area.

    Args:
        sender_agent_id: The agent attaching the files (authenticated context).
        owner_user_id: The owning user (both agents' workspace root).
        refs: Handles — ``att_...`` file_ids and/or workspace-relative paths.
        base: Override base_working_path (tests); defaults to settings.

    Returns:
        List of bus-attachment dicts. Unresolvable handles are logged and
        skipped — a bad attachment must never abort the message send.
    """
    base = _base(base)
    dest_dir = bus_files_dir(owner_user_id, base) / _today_str()
    out: List[dict] = []
    for ref in refs:
        src = _resolve_ref_to_source(ref, sender_agent_id, owner_user_id, base)
        if src is None:
            continue
        try:
            out.append(await _stage_into(src, dest_dir, base, original_name=src.name))
        except Exception as e:  # noqa: BLE001 — one bad file must not drop the message
            logger.warning(f"[bus attachment] failed to stage {ref!r}: {e}")
    return out


async def stage_path_into_team(
    *,
    sender_agent_id: str,
    owner_user_id: str,
    team_id: str,
    ref: str,
    base: Optional[str] = None,
) -> Optional[dict]:
    """Stage one workspace file (or att_ file_id) into a team's shared scratch
    dir. Returns the staged dict with an added absolute ``path``, or None if the
    handle does not resolve. Membership/ownership are validated by the caller."""
    base = _base(base)
    src = _resolve_ref_to_source(ref, sender_agent_id, owner_user_id, base)
    if src is None:
        return None
    dest_dir = team_shared_dir(owner_user_id, team_id, base)
    staged = await _stage_into(src, dest_dir, base, original_name=src.name)
    staged["path"] = str((_base_root(base) / staged["rel_path"]).resolve())
    return staged


def build_bus_markers(
    attachments: Optional[List[dict]],
    *,
    from_agent: str = "",
    base: Optional[str] = None,
) -> str:
    """Render bus-attachment dicts as newline-joined Read-tool markers.

    Same shape as ``Attachment.synthesize_marker`` so recipient behaviour is
    uniform with user-uploaded files — the agent sees an absolute path and a
    ``use Read tool`` instruction. The absolute path is rebuilt from
    ``base_working_path`` + the stored base-relative ``rel_path`` (drift-tolerant,
    like ``instance_artifacts.file_path``). Empty/malformed input → "".
    """
    if not attachments:
        return ""
    root = _base_root(_base(base))
    origin = f" from agent {from_agent}" if from_agent else ""
    lines: List[str] = []
    for att in attachments:
        if not isinstance(att, dict):
            continue
        rel = att.get("rel_path")
        if not rel:
            continue
        path = str((root / rel).resolve())
        name = att.get("original_name") or "(unnamed)"
        mime = att.get("mime_type") or "application/octet-stream"
        kind = att.get("category") or "file"
        marker = f"[Shared file{origin}: name={name}, path={path}, mime={mime}, kind={kind}"
        transcript = att.get("transcript")
        if isinstance(transcript, str) and transcript.strip():
            # A voice memo — surface the spoken text inline so the recipient
            # agent reads it directly (it cannot listen to the audio).
            marker += f", transcript={transcript.strip()}"
        marker += " — use Read tool to view]"
        lines.append(marker)
    return "\n".join(lines)


def resolve_shared_file_by_id(
    user_id: str, file_id: str, base: Optional[str] = None
) -> Optional[Path]:
    """Resolve a bus-attachment ``file_id`` to its file in the shared area.

    The shared bus area keeps no ``_index.json`` (the DB row is the index), so
    resolution-by-id globs ``{base}/{user_id}/_shared/bus_files/*/{file_id}.*``.
    Prefers the ORIGINAL upload over a ``.mp3`` transcode sibling. Used by the
    public transcription endpoint as a fallback when the agent-scoped resolver
    misses (team voice memos live here, not in an agent's user_upload_files).
    Scoped to the caller's own user root; validates the file_id format.
    """
    if not is_valid_file_id(file_id):
        return None
    root = bus_files_dir(user_id, _base(base))
    if not root.exists():
        return None
    matches = sorted(root.glob(f"*/{file_id}.*")) + sorted(root.glob(f"*/{file_id}"))
    original = next((p for p in matches if p.is_file() and p.suffix.lower() != ".mp3"), None)
    if original is not None:
        return original
    return next((p for p in matches if p.is_file()), None)


def resolve_shared_file_for_user(
    user_id: str, rel_path: str, base: Optional[str] = None
) -> Optional[Path]:
    """Resolve a bus-attachment ``rel_path`` to an absolute file for serving.

    Used by the frontend download endpoints. The caller passes the
    AUTHENTICATED ``user_id``; the ``rel_path`` comes from a message's stored
    attachment dict (and is therefore attacker-influenceable over the wire), so
    we gate it hard:

      * the path's first segment MUST equal ``user_id`` (a user may only fetch
        files under their own per-user root — which is also the only place bus
        attachments for their agents can live);
      * the resolved absolute path MUST stay within ``{base}/{user_id}`` (blocks
        ``../`` traversal);
      * the file must exist.

    Returns the absolute path, or None on any check failure (caller → 404).
    """
    base = _base(base)
    rel = (rel_path or "").replace("\\", "/").lstrip("/")
    parts = rel.split("/")
    if not parts or parts[0] != user_id:
        return None
    user_root = (_base_root(base) / user_id).resolve()
    candidate = (_base_root(base) / rel).resolve()
    if not candidate.is_relative_to(user_root):
        return None
    if not candidate.is_file():
        return None
    return candidate
