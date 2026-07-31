"""
@file_name: files.py
@author: Bin Liang
@date: 2026-07-29
@description: The file six-pack — read_file / write_file / edit_file /
glob / grep / ls. Spec (description included) and handler live together:
one source of truth, no prompt drift. All paths resolve inside the
workspace; the confinement policy has already vetted them when a
handler runs.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path

from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolAnnotations,
    ToolContext,
    ToolResult,
    ToolSpec,
)

_MAX_READ_CHARS = 100_000
_MAX_MATCHES = 200
_MAX_GREP_FILE_BYTES = 2_000_000
_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv"}


def specs() -> list[ToolSpec]:
    read_only = ToolAnnotations(read_only=True)
    return [
        ToolSpec(
            name="read_file",
            description=(
                "Read a text file from the workspace. Supports offset/limit "
                "line windows for large files; output beyond the size cap is "
                "truncated with an explicit marker."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path (workspace-relative or absolute inside the workspace)."},
                    "offset": {"type": "integer", "description": "1-based first line to read.", "minimum": 1},
                    "limit": {"type": "integer", "description": "Maximum number of lines.", "minimum": 1},
                },
                "required": ["path"],
            },
            annotations=read_only,
        ),
        ToolSpec(
            name="write_file",
            description=(
                "Write content to a file (full replacement). Parent "
                "directories are created as needed."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        ),
        ToolSpec(
            name="edit_file",
            description=(
                "Exact string replacement in a file. `old` must occur exactly "
                "once (extend it with more context if ambiguous); use "
                "`replace_all` to replace every occurrence."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old": {"type": "string"},
                    "new": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old", "new"],
            },
        ),
        ToolSpec(
            name="glob",
            description=(
                "Find files by glob pattern (e.g. '**/*.py'), newest first, "
                "up to a result cap."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "description": "Directory to search from (default: workspace root)."},
                },
                "required": ["pattern"],
            },
            annotations=read_only,
        ),
        ToolSpec(
            name="grep",
            description=(
                "Search file contents with a regular expression; returns "
                "matching lines as path:line:text, up to a match cap."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Python-flavored regular expression."},
                    "path": {"type": "string", "description": "Directory to search (default: workspace root)."},
                    "glob": {"type": "string", "description": "Only search files matching this glob (e.g. '*.py')."},
                },
                "required": ["pattern"],
            },
            annotations=read_only,
        ),
        ToolSpec(
            name="ls",
            description="List a directory (default: workspace root) with entry types and sizes.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            annotations=read_only,
        ),
    ]


def _resolve(ctx: ToolContext, raw: str | None) -> Path:
    """Directory tools may default to the workspace root; FILE tools must
    not (see ``_require_path``): a per-file handler that silently lands
    on the root turns "argument never arrived" into ``Is a directory``."""
    workspace = Path(ctx.workspace).resolve()
    if not raw:
        return workspace
    candidate = Path(raw)
    return (candidate if candidate.is_absolute() else workspace / candidate).resolve()


def _require_path(call_id: str, args: dict) -> ToolResult | None:
    """The dispatcher already validates required fields; the tool that
    touches the filesystem does not get to assume someone else checked
    (same lesson as the glob climb-out, 2026-07-29 review)."""
    if not args.get("path"):
        return ToolResult(
            call_id=call_id, ok=False, error="missing required argument: path"
        )
    return None


def _truncate(text: str, cap: int = _MAX_READ_CHARS) -> str:
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n[truncated {len(text) - cap} chars]"


async def read_file(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    if (guard := _require_path(call_id, args)) is not None:
        return guard
    path = _resolve(ctx, args.get("path"))
    if not path.is_file():
        return ToolResult(call_id=call_id, ok=False, error=f"not a file: {path}")
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return ToolResult(call_id=call_id, ok=False, error=str(exc))
    offset = int(args.get("offset") or 1)
    limit = args.get("limit")
    if offset > 1 or limit is not None:
        lines = text.splitlines()
        end = offset - 1 + int(limit) if limit is not None else len(lines)
        text = "\n".join(lines[offset - 1 : end])
    return ToolResult(call_id=call_id, ok=True, content=_truncate(text))


async def write_file(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    if (guard := _require_path(call_id, args)) is not None:
        return guard
    path = _resolve(ctx, args.get("path"))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(args.get("content", "")), encoding="utf-8")
    except OSError as exc:
        return ToolResult(call_id=call_id, ok=False, error=str(exc))
    return ToolResult(call_id=call_id, ok=True, content=f"wrote {path}")


async def edit_file(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    if (guard := _require_path(call_id, args)) is not None:
        return guard
    path = _resolve(ctx, args.get("path"))
    if not path.is_file():
        return ToolResult(call_id=call_id, ok=False, error=f"not a file: {path}")
    old, new = str(args.get("old", "")), str(args.get("new", ""))
    if not old:
        return ToolResult(call_id=call_id, ok=False, error="`old` must be non-empty")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        return ToolResult(call_id=call_id, ok=False, error="`old` not found in file")
    if count > 1 and not args.get("replace_all"):
        return ToolResult(
            call_id=call_id,
            ok=False,
            error=f"`old` occurs {count} times; extend it or set replace_all",
        )
    path.write_text(text.replace(old, new), encoding="utf-8")
    replaced = count if args.get("replace_all") else 1
    return ToolResult(call_id=call_id, ok=True, content=f"replaced {replaced} occurrence(s)")


async def glob_files(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    root = _resolve(ctx, args.get("path"))
    pattern = str(args.get("pattern", "*"))
    if not root.is_dir():
        return ToolResult(call_id=call_id, ok=False, error=f"not a directory: {root}")
    # The pattern is a path expression, so it can climb: `../../etc/*`
    # walked out of the workspace entirely (2026-07-29 review). The policy
    # layer now rejects such patterns up front, but the tool that actually
    # touches the filesystem does not get to assume someone else checked —
    # that assumption is what opened the hole. Filtering here also removes
    # a latent crash, since `relative_to(root)` below raises on an outside
    # hit.
    hits = [
        p
        for p in root.glob(pattern)
        if p.is_file() and p.resolve().is_relative_to(root)
    ]
    hits.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    shown = [str(p.relative_to(root)) for p in hits[:_MAX_MATCHES]]
    suffix = "" if len(hits) <= _MAX_MATCHES else f"\n[{len(hits) - _MAX_MATCHES} more not shown]"
    return ToolResult(call_id=call_id, ok=True, content="\n".join(shown) + suffix)


async def grep_files(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    root = _resolve(ctx, args.get("path"))
    try:
        pattern = re.compile(str(args.get("pattern", "")))
    except re.error as exc:
        return ToolResult(call_id=call_id, ok=False, error=f"bad regex: {exc}")
    file_glob = args.get("glob")
    matches: list[str] = []
    for path in sorted(root.rglob("*")):
        if len(matches) >= _MAX_MATCHES:
            break
        if not path.is_file() or any(part in _SKIP_DIRS for part in path.parts):
            continue
        if file_glob and not fnmatch.fnmatch(path.name, str(file_glob)):
            continue
        try:
            if path.stat().st_size > _MAX_GREP_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if pattern.search(line):
                rel = path.relative_to(root)
                matches.append(f"{rel}:{lineno}:{line.strip()[:300]}")
                if len(matches) >= _MAX_MATCHES:
                    break
    body = "\n".join(matches) if matches else "(no matches)"
    return ToolResult(call_id=call_id, ok=True, content=_truncate(body))


async def list_dir(call_id: str, args: dict, ctx: ToolContext) -> ToolResult:
    path = _resolve(ctx, args.get("path"))
    if not path.is_dir():
        return ToolResult(call_id=call_id, ok=False, error=f"not a directory: {path}")
    rows = []
    for entry in sorted(path.iterdir(), key=lambda p: (p.is_file(), p.name)):
        if entry.is_dir():
            rows.append(f"{entry.name}/")
        else:
            rows.append(f"{entry.name}  ({entry.stat().st_size} bytes)")
    return ToolResult(call_id=call_id, ok=True, content="\n".join(rows) or "(empty)")


HANDLERS = {
    "read_file": read_file,
    "write_file": write_file,
    "edit_file": edit_file,
    "glob": glob_files,
    "grep": grep_files,
    "ls": list_dir,
}
