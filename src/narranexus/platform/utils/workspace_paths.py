"""
@file_name: workspace_paths.py
@author:
@date: 2026-06-17
@description: Single source of truth for an agent's on-disk workspace layout.

Historically the layout ``{base_working_path}/{agent_id}_{user_id}`` was
hardcoded as ``f"{agent_id}_{user_id}"`` in ~10 places (step_3, bundle,
bootstrap, skill_module, attachment_storage, ...). That made it
impossible to change the layout without hunting every call site.

This module centralizes it. Today it returns the legacy FLAT name so the
conversion is behaviour-identical. The next step flips
``_LAYOUT`` to the per-user nested form ``{user_id}/{agent_id}`` — which
is what lets a per-user Executor container bind-mount only
``{base}/{user_id}`` and thereby see ONLY that user's agents (cross-user
file isolation by mount, no uid tricks needed). When that flip happens,
ONLY this module changes (plus a one-off data migration); every call
site already routes through here.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Layout selector. "flat" = legacy ``{agent_id}_{user_id}`` (current on
# disk). "nested" = ``{user_id}/{agent_id}`` (per-user mount isolation).
# Flip to "nested" together with the data migration — never before, or
# running agents lose their workspace.
_LAYOUT = "nested"


def safe_id_segment(value: str, *, label: str) -> str:
    """Assert ``value`` is usable as ONE directory name under the base dir.

    Every id that becomes a path component (agent_id, user_id, team_id)
    passes through here. The rule is deliberately about path safety, not
    about id shape: legacy rows carry user ids with spaces and agent ids
    like ``agent_a_abb17376``, and those must keep resolving. What can never
    be allowed is anything that moves the path up or sideways — separators,
    NUL, ``.``/``..``, or a dot-led name (dotdirs are hidden tooling state and
    ``..;``-style variants are how the 2026-10-28 cross-tenant read got in:
    ``agent_id=".."`` collapsed ``{base}/{user}/..`` to the base itself).

    Raises ValueError; HTTP callers map that to a 4xx before touching disk.
    """
    if not value or "\x00" in value or "/" in value or "\\" in value:
        raise ValueError(f"invalid {label}: not a single path segment")
    if value.startswith("."):
        raise ValueError(f"invalid {label}: dot-led names are reserved")
    return value


def agent_workspace_relpath(agent_id: str, user_id: str) -> str:
    """Workspace path of one agent, RELATIVE to base_working_path.

    A POSIX-style relative path (may contain a ``/`` once the layout is
    nested). Callers that need an absolute path use
    :func:`agent_workspace_path`.
    """
    safe_id_segment(agent_id, label="agent_id")
    safe_id_segment(user_id, label="user_id")
    if _LAYOUT == "nested":
        return f"{user_id}/{agent_id}"
    return f"{agent_id}_{user_id}"


def agent_workspace_path(
    agent_id: str, user_id: str, base: Optional[str] = None
) -> Path:
    """Absolute workspace path for one agent.

    Args:
        base: base working dir; defaults to ``settings.base_working_path``.
    """
    if base is None:
        from narranexus.platform.settings import settings
        base = settings.base_working_path
    return Path(base) / agent_workspace_relpath(agent_id, user_id)


# ---------------------------------------------------------------------------
# Per-user SHARED areas (cross-agent, same-user collaboration)
# ---------------------------------------------------------------------------
#
# These live under the per-user root ``{base}/{user_id}`` — the exact subtree a
# per-user Executor bind-mounts — so every agent OWNED BY THE SAME USER can
# ``Read`` files here in both local and cloud mode (the message bus forbids
# cross-user messaging, so "same user" is the only sharing scope that exists).
# The shared area is deliberately a SIBLING of each agent's own workspace dir,
# never inside one, so no single agent "owns" it.

_SHARED_DIRNAME = "_shared"
_BUS_FILES_DIRNAME = "bus_files"
_TEAMS_DIRNAME = "teams"


def _shared_base(base: Optional[str]) -> str:
    if base is None:
        from narranexus.platform.settings import settings
        base = settings.base_working_path
    return os.path.realpath(base)  # platform-owned; the tenant tail stays lexical


def refuse_symlinked_dir(path: "Path | str", *, label: str) -> None:
    """Fail closed when a tenant-writable directory has been swapped for a link.

    Every workspace / team folder sits inside the owner's bind mount, so the
    owner can replace it with a symlink to another tenant's tree; any code
    that then resolves paths "inside the workspace" would be inside the
    victim's. Raises ValueError.
    """
    if os.path.islink(path):
        raise ValueError(f"{label} is a symlink: {path}")


def resolve_inside_workspace(workspace: "Path | str", candidate: "str | Path") -> Path:
    """Resolve ``candidate`` and prove it sits inside ``workspace``.

    The one sanctioned way for host-side code to turn an agent-supplied path
    (relative to the workspace, or absolute) into a file it will read, copy,
    send or watch. ``workspace`` is used LEXICALLY and must be built on a
    realpath-ed base (``resolve_existing_workspace`` does that; otherwise pass
    ``base=os.path.realpath(base)``): the tenant tail is inside the tenant's own
    mount and may have been swapped for a symlink to another tenant's tree, so
    resolving it would make the link target "the workspace". The candidate IS
    resolved, so ``..``, an absolute path elsewhere, or a link anywhere on its
    path lands outside the lexical root and fails closed.

    Raises:
        ValueError: the workspace is a symlink, or the candidate escapes it.
            Callers wrap it in their own error type.
    """
    root = Path(os.path.abspath(workspace))  # normalise only; never follows links
    refuse_symlinked_dir(root, label="agent workspace")
    target = (root / candidate).resolve()  # an absolute candidate replaces root
    try:
        target.relative_to(root)
    except ValueError:
        raise ValueError(f"path escapes the agent workspace: {candidate}") from None
    return target


def user_shared_root(user_id: str, base: Optional[str] = None) -> Path:
    """Absolute per-user shared root: ``{base}/{user_id}/_shared``."""
    safe_id_segment(user_id, label="user_id")
    return Path(_shared_base(base)) / user_id / _SHARED_DIRNAME


def bus_files_dir(user_id: str, base: Optional[str] = None) -> Path:
    """Absolute drop dir for files attached to bus messages:
    ``{base}/{user_id}/_shared/bus_files``. Date-partitioned by the caller."""
    return user_shared_root(user_id, base) / _BUS_FILES_DIRNAME


def team_shared_dir(user_id: str, team_id: str, base: Optional[str] = None) -> Path:
    """Absolute per-team shared scratch dir:
    ``{base}/{user_id}/_shared/teams/{team_id}``. A common space for a team's
    agents to hand off collaborative artifacts beyond single-message attachments."""
    safe_id_segment(team_id, label="team_id")
    return user_shared_root(user_id, base) / _TEAMS_DIRNAME / team_id


def turn_accessible_roots(
    user_id: str, team_id: Optional[str] = None, base: Optional[str] = None
) -> tuple[str, ...]:
    """Absolute roots one turn may reach OUTSIDE its own agent workspace.

    "Accessible", not "readable": the confinement layers that consume this
    inspect `file_path` and shell paths too, so the same grant governs Write,
    Edit and rm. Naming it after reading would understate it.

    Two entries, and they are different in kind:

    * ``bus_files`` — user-wide by design. The bus stages an attachment ONCE
      into the owner's shared area and every same-user recipient reads that
      one path; narrowing it per team would break message delivery, which
      acceptance #6 covers explicitly.
    * ``teams/{team_id}`` — ONLY the team this turn belongs to. One owner has
      many teams, so granting the whole ``_shared`` tree (as the first version
      did) handed every team's folder to every turn, including one-to-one
      chats that belong to no team. That is the same owner-vs-membership
      mistake `list_for_agent_context` documents as the cross-team leak.

    The parent ``_shared`` is deliberately never returned: it would re-admit
    every team through one entry.

    Args:
        user_id: Owner of the turn.
        team_id: Team whose room the turn runs in; None/"" outside a team.
        base: Workspace root override (tests).

    Returns:
        Absolute paths, always including the bus attachment dir.
    """
    roots = [str(bus_files_dir(user_id, base))]
    if team_id:
        roots.append(str(team_shared_dir(user_id, team_id, base)))
    return tuple(roots)


def workspace_candidate_relpaths(agent_id: str, user_id: str) -> list[str]:
    """All workspace-dir relpath forms, current layout first then legacy.

    Used by readers of EXISTING data so a row/dir written under the old flat
    layout still resolves after the flip to nested (and vice-versa), without
    a database rewrite. Order matters: the current layout wins.

    Public because the artifact owner-root confinement
    (``artifact/_artifact_impl/confinement.owner_roots``) treats every form as
    a legitimate home of that agent; a signature change here changes which
    roots an artifact may be served from.
    """
    return [
        agent_workspace_relpath(agent_id, user_id),   # current layout
        f"{agent_id}_{user_id}",                       # legacy flat
        f"{agent_id}_{_LEGACY_INFIX}{user_id}",        # legacy `_user_` infix
    ]


def resolve_existing_workspace(
    agent_id: str, user_id: str, base: Optional[str] = None
) -> Path:
    """Workspace dir for reads/cleanup — the first candidate that EXISTS,
    preferring the current layout, falling back to legacy forms. Returns the
    current-layout path if none exist (so callers get a sensible default).
    """
    if base is None:
        from narranexus.platform.settings import settings
        base = settings.base_working_path
    # The base is platform-owned, so resolving it is safe; the per-tenant tail
    # is appended LEXICALLY and never resolved — a tenant can turn its own
    # workspace dir into a symlink, and resolving here would make the link's
    # target "the workspace" (2026-10-28). Callers that realpath an entry can
    # then compare it against this lexical root and fail closed.
    root = Path(os.path.realpath(base))
    for rel in workspace_candidate_relpaths(agent_id, user_id):
        p = root / rel
        if p.is_dir():
            return p
    return root / agent_workspace_relpath(agent_id, user_id)


def resolve_workspace_relative_file(
    file_path: str, agent_id: str, user_id: str, base: Optional[str] = None
) -> Path:
    """Resolve a base-relative file path that carries a workspace prefix
    (e.g. ``instance_artifacts.file_path``) to an absolute path that EXISTS,
    tolerating a layout mismatch between when it was stored and now.

    Tries ``base/file_path`` as-is first (covers rows already in the current
    layout). If that's missing, strips whichever known workspace prefix the
    stored value carries and re-prepends the CURRENT relpath. Falls back to
    the as-is join so a genuinely-missing file still 404s meaningfully.
    """
    if base is None:
        from narranexus.platform.settings import settings
        base = settings.base_working_path
    root = Path(base)
    direct = root / file_path
    if direct.exists():
        return direct
    norm = file_path.replace("\\", "/")
    for rel in workspace_candidate_relpaths(agent_id, user_id):
        prefix = rel + "/"
        if norm.startswith(prefix):
            rest = norm[len(prefix):]
            cand = agent_workspace_path(agent_id, user_id, base=base) / rest
            if cand.exists():
                return cand
            break
    return direct


# ---------------------------------------------------------------------------
# One-off migration: flat ``{agent_id}_{user_id}`` → nested ``{user_id}/{agent_id}``
# ---------------------------------------------------------------------------

_LEGACY_INFIX = "user_"


def _parse_flat_dirname(
    name: str, known_user_ids: set[str]
) -> Optional[tuple[str, str]]:
    """Parse a legacy flat workspace dir name into (agent_id, user_id).

    Agent ids are ``agent_<hex>`` (single token, no internal ``_``), so the
    name is ``agent_<hex>_<rest>``. ``<rest>`` is AMBIGUOUS: it could be the
    user_id directly (canonical ``{agent}_{user}``) OR the legacy
    ``{agent}_user_{user}`` infix form — e.g. ``agent_x_user_binliang`` is
    user ``binliang`` (infix), NOT user ``user_binliang``. Dir names alone
    cannot disambiguate, so we resolve against the authoritative set of
    real user ids from the DB.

    Returns None when neither interpretation matches a known user (an
    orphan / unknown dir — never guessed, left in place by the caller).
    """
    if not name.startswith("agent_"):
        return None
    parts = name.split("_")
    if len(parts) < 3:
        return None
    agent_id = f"{parts[0]}_{parts[1]}"
    rest = "_".join(parts[2:])
    if rest in known_user_ids:
        return agent_id, rest
    if rest.startswith(_LEGACY_INFIX):
        candidate = rest[len(_LEGACY_INFIX):]
        if candidate in known_user_ids:
            return agent_id, candidate
    return None


def migrate_flat_to_nested(
    base: str, known_user_ids: set[str], dry_run: bool = False
) -> dict:
    """Rename legacy flat workspace dirs to the nested per-user layout.

    ``known_user_ids`` is the authoritative set of real user ids (from the
    DB ``users`` table) — REQUIRED to disambiguate the legacy ``_user_``
    infix form from a real user id that happens to start with ``user_``.

    Idempotent and non-destructive:
      - only top-level ``agent_*_*`` dirs whose owner resolves to a known
        user are moved;
      - if the nested target already exists, the flat dir is left in place
        (reported as a conflict — never overwritten / deleted);
      - dirs that don't resolve to a known user are left in place
        (reported as ``unknown``), never guessed;
      - already-nested user dirs are skipped.

    Run once at deploy (see ``scripts/data_migrations/migrate_workspace_layout.py``) BEFORE
    flipping ``_LAYOUT`` to "nested". Returns a report dict.
    """
    root = Path(base)
    report: dict = {"moved": [], "skipped": [], "conflicts": [], "unknown": []}
    if not root.is_dir():
        return report
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        if not entry.name.startswith("agent_"):
            report["skipped"].append(entry.name)
            continue
        parsed = _parse_flat_dirname(entry.name, known_user_ids)
        if parsed is None:
            report["unknown"].append(entry.name)
            continue
        agent_id, user_id = parsed
        target = root / user_id / agent_id
        if target.exists():
            report["conflicts"].append(entry.name)
            continue
        report["moved"].append((entry.name, f"{user_id}/{agent_id}"))
        if not dry_run:
            target.parent.mkdir(parents=True, exist_ok=True)
            os.rename(entry, target)
    return report
