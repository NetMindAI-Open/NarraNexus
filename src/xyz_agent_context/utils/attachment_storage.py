"""
@file_name: attachment_storage.py
@author: Bin Liang
@date: 2026-04-29
@description: Workspace-scoped storage for user-uploaded chat attachments

Layout
------
Files live under each agent's workspace at:

    {workspace}/{agent_id}_{user_id}/user_upload_files/{YYYY-MM-DD}/{file_id}{ext}

A per-day `_index.json` records the metadata needed to map a `file_id` back
to its on-disk path without scanning the directory:

    {
        "att_a1b2c3d4": {
            "filename": "att_a1b2c3d4.png",
            "original_name": "cat.jpg",
            "mime_type": "image/png",
            "size_bytes": 12345,
            "created_at": "2026-04-29T03:14:15.123456+00:00"
        },
        ...
    }

Why a sidecar index instead of a SQL table:
- Zero schema changes; works the same in SQLite and MySQL backends.
- Lookup is bounded — the resolver scans today's index then yesterday's,
  so worst case is two small file reads. If volume grows we'll migrate to
  a real `instance_attachments` table in Phase 2.

Security
--------
All paths returned by `resolve_attachment_path` are validated to live under
the agent's workspace via `ensure_within_directory`. Callers must pass the
real `agent_id` / `user_id` from authenticated request context — never
accept those from the LLM as tool arguments.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Tuple

from xyz_agent_context.schema.attachment_schema import (
    FILE_ID_PREFIX,
    FILE_ID_REGEX,
)
from xyz_agent_context.utils.file_safety import (
    ensure_within_directory,
    sanitize_filename,
)


_FILE_ID_REGEX_C = re.compile(FILE_ID_REGEX)
_INDEX_FILENAME = "_index.json"
_USER_UPLOAD_SUBDIR = "user_upload_files"
# How many recent days to scan when resolving a file_id (today + N-1 prior).
# Two days covers any pre-/post-midnight upload race; bigger lookbacks would
# only matter for sessions that pause for multiple days, which we don't model.
_RESOLVER_LOOKBACK_DAYS = 2


def generate_file_id() -> str:
    """Return a fresh attachment file_id ('att_' + 8 lowercase hex)."""
    return f"{FILE_ID_PREFIX}{secrets.token_hex(4)}"


def is_valid_file_id(file_id: str) -> bool:
    """Validate a file_id matches the expected pattern."""
    return bool(_FILE_ID_REGEX_C.match(file_id or ""))


def get_workspace_path(agent_id: str, user_id: str) -> Path:
    """Return the agent-user workspace root.

    Mirrors `backend.routes.agents_files._get_workspace_path` so file uploads
    from chat live alongside the workspace files the agent already manages.
    Imported lazily so this util has no FastAPI dependency at import time.
    """
    from xyz_agent_context.settings import settings as core_settings
    return Path(core_settings.base_working_path) / f"{agent_id}_{user_id}"


def _today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _date_dir(workspace: Path, date_str: str) -> Path:
    return workspace / _USER_UPLOAD_SUBDIR / date_str


def _read_index(date_dir: Path) -> dict:
    index_path = date_dir / _INDEX_FILENAME
    if not index_path.exists():
        return {}
    try:
        with index_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        # A corrupt index should not poison resolution — log via the caller.
        return {}


def _write_index(date_dir: Path, index: dict) -> None:
    index_path = date_dir / _INDEX_FILENAME
    tmp_path = index_path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, index_path)


def store_uploaded_attachment(
    agent_id: str,
    user_id: str,
    *,
    raw_bytes: bytes,
    original_name: str,
    mime_type: str,
) -> Tuple[str, Path]:
    """Persist an upload and update the per-day index.

    Returns (file_id, absolute_path). The caller is responsible for size
    validation and MIME sniffing before calling — this function trusts what
    it receives but still sanitizes the on-disk filename.
    """
    workspace = get_workspace_path(agent_id, user_id)
    date_str = _today_str()
    date_dir = _date_dir(workspace, date_str)
    date_dir.mkdir(parents=True, exist_ok=True)

    # Derive the on-disk extension from the (sanitized) original filename.
    # Fall back to no-extension if the user uploaded e.g. 'screenshot' with
    # no suffix; the MIME type stored in the index is still authoritative.
    safe_original = sanitize_filename(original_name or "upload", label="filename")
    suffix = Path(safe_original).suffix.lower()

    file_id = generate_file_id()
    on_disk_name = f"{file_id}{suffix}" if suffix else file_id
    target_path = ensure_within_directory(date_dir, on_disk_name, label="attachment")

    with target_path.open("wb") as f:
        f.write(raw_bytes)

    index = _read_index(date_dir)
    index[file_id] = {
        "filename": on_disk_name,
        "original_name": safe_original,
        "mime_type": mime_type,
        "size_bytes": len(raw_bytes),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    _write_index(date_dir, index)

    return file_id, target_path


def resolve_attachment_path(
    agent_id: str,
    user_id: str,
    file_id: str,
) -> Optional[Path]:
    """Resolve a file_id to an absolute path inside the agent workspace.

    Returns None if the file_id format is invalid, the file is missing, or
    the resolved path would escape the workspace (defense in depth — the
    `_index.json` should never contain unsafe names but we re-validate).
    """
    if not is_valid_file_id(file_id):
        return None
    workspace = get_workspace_path(agent_id, user_id)
    if not workspace.exists():
        return None

    today = datetime.now(timezone.utc).date()
    for offset in range(_RESOLVER_LOOKBACK_DAYS):
        date_str = (today.fromordinal(today.toordinal() - offset)).strftime("%Y-%m-%d")
        date_dir = _date_dir(workspace, date_str)
        if not date_dir.exists():
            continue
        entry = _read_index(date_dir).get(file_id)
        if not entry:
            continue
        try:
            candidate = ensure_within_directory(
                date_dir, entry["filename"], label="attachment"
            )
        except ValueError:
            return None
        if candidate.exists():
            return candidate

    # Fallback: directly scan the user_upload_files tree. Useful when the
    # index is missing/corrupt for some date we haven't tracked. Bounded by
    # the number of date subdirectories — small in practice.
    upload_root = workspace / _USER_UPLOAD_SUBDIR
    if upload_root.exists():
        for date_dir in sorted(upload_root.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            entry = _read_index(date_dir).get(file_id)
            if not entry:
                continue
            try:
                candidate = ensure_within_directory(
                    date_dir, entry["filename"], label="attachment"
                )
            except ValueError:
                continue
            if candidate.exists():
                return candidate

    return None


def format_attachments_for_system_prompt(
    attachments: list,
    agent_id: str,
    user_id: str,
) -> str:
    """Render the current-turn attachment list as a system-prompt block.

    Used by CommonToolsModule.get_instructions to inject the resolved
    absolute paths of files the user uploaded with the latest message.
    The agent reads this block alongside the static Read instruction, so
    it knows which paths are "live" right now without having to scan
    the workspace.

    Returns an empty string when there are no attachments — the caller
    can unconditionally append the result to the base instruction text.
    """
    if not attachments:
        return ""
    lines: list[str] = ["#### Files attached to the current message"]
    lines.append(
        "The user just uploaded the following file(s) with this message. "
        "Call the built-in `Read` tool with the absolute path to view each one. "
        "Audio uploads include an inline `transcript=...` field — read that "
        "text directly instead of attempting to play the audio. If an audio "
        "file is missing its `transcript` field, transcription was "
        "unavailable (typically because the user has not configured an "
        "OpenAI-compatible provider). In that case, briefly tell the user "
        "the audio could not be transcribed and ask them to add an OpenAI "
        "API key under Settings → Providers, then resend the audio."
    )
    for att in attachments:
        if not isinstance(att, dict):
            continue
        file_id = att.get("file_id", "")
        name = att.get("original_name") or att.get("name") or "(unnamed)"
        mime = att.get("mime_type") or "application/octet-stream"
        # `category` may be a plain string (WS payload, JSON memory) or an
        # AttachmentCategory enum (Pydantic model_dump without mode=json).
        category = att.get("category") or "file"
        if hasattr(category, "value"):
            category = category.value
        path = resolve_attachment_path(agent_id, user_id, file_id)
        path_str = str(path) if path is not None else "<unavailable>"
        line = f"- name={name}, type={category}, mime={mime}, path={path_str}"
        transcript = att.get("transcript")
        if isinstance(transcript, str) and transcript.strip():
            line += f", transcript={transcript.strip()}"
        elif isinstance(mime, str) and mime.startswith("audio/"):
            # Audio with no transcript → tell the agent why, so it can
            # explain to the user instead of saying "I can't listen".
            line += (
                ", transcript=<unavailable: no OpenAI-compatible provider "
                "configured; ask user to add an OpenAI API key in "
                "Settings → Providers>"
            )
        lines.append(line)
    return "\n".join(lines)


def get_attachment_metadata(
    agent_id: str,
    user_id: str,
    file_id: str,
) -> Optional[dict]:
    """Look up the index entry for a file_id without opening the file."""
    if not is_valid_file_id(file_id):
        return None
    workspace = get_workspace_path(agent_id, user_id)
    if not workspace.exists():
        return None

    upload_root = workspace / _USER_UPLOAD_SUBDIR
    if not upload_root.exists():
        return None

    for date_dir in sorted(upload_root.iterdir(), reverse=True):
        if not date_dir.is_dir():
            continue
        entry = _read_index(date_dir).get(file_id)
        if entry:
            return entry
    return None
