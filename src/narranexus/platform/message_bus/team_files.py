"""
@file_name: team_files.py
@author: NarraNexus
@date: 2026-08-07
@description: Enumerate a team's shared folder.

The folder has always held files; what it lacked was any way to ask what is
in it. Staging names files by generated id, so the original names live only in
the index — before that existed, discovery was one agent reciting an absolute
path in the room and the others hoping to notice. That made "did you get the
file" a social protocol between models, reliable exactly as often as the
models remembered to narrate.

Kept out of the MCP tool module so the rule that matters — membership, not
ownership — is testable without an MCP transport, and so the same function can
back a route later.
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from narranexus.platform.repository.team_workspace_repository import TeamFileRepository
from narranexus.platform.utils.workspace_paths import team_shared_dir

#: Cap on one listing. Large enough that a real team folder fits, small enough
#: that a runaway one cannot blow up the model's context in a single call.
MAX_TEAM_FILES = 200


async def list_team_files(
    *,
    db,
    agent_id: str,
    team_id: str,
    base: Optional[str] = None,
) -> dict[str, Any]:
    """List the files shared into ``team_id``, newest first.

    Authorisation is MEMBERSHIP, deliberately not ownership. One user owns many
    teams, so "this is my owner's team" is not permission to read it — that
    check would let any agent of the owner read every team the owner has.

    Args:
        db: Active database client.
        agent_id: The calling agent (server-resolved; see module/_mcp_identity).
        team_id: Team whose folder to list.
        base: Workspace root override (tests).

    Returns:
        ``{"success": True, "files": [...]}`` — possibly empty, which is an
        answer, not a failure. On refusal ``{"success": False, "error": str}``
        with no ``files`` key, so a caller cannot mistake a refusal for an
        empty folder.
    """
    try:
        team = await db.get_one("teams", {"team_id": team_id})
        if not team:
            return {"success": False, "error": f"team not found: {team_id}"}

        membership = await db.get_one(
            "team_members", {"team_id": team_id, "agent_id": agent_id}
        )
        if not membership:
            return {"success": False, "error": "you are not a member of this team"}

        # Through the repository like every other reader of this table. The
        # third hand-written copy was the whole reason that seam exists, and it
        # was also the only `team_files` statement with a bound LIMIT — the one
        # shape drivers disagree about — with no MySQL case of its own.
        rows = await TeamFileRepository(db).list_by_team(team_id, limit=MAX_TEAM_FILES)
        root = team_shared_dir(team["owner_user_id"], team_id, base).parent.parent.parent
        files = [
            {
                "name": r["original_name"],
                # Absolute, because that is what Read takes and what the team
                # prompt already tells agents to use. A listing the agent
                # cannot act on would just be prose.
                "path": str(root / r["rel_path"]),
                "size_bytes": int(r["size_bytes"]),
                "shared_by": r["shared_by_agent_id"],
                "shared_at": r["created_at"],
            }
            for r in rows
        ]
        return {"success": True, "files": files}
    except Exception as e:  # noqa: BLE001 — tool surface returns structured errors
        # Widening watch: this catch now also sits over the repository, so it
        # can swallow things that are not "you cannot list this" — a malformed
        # `created_at` reaching `parse_dt`, say, would reach the agent as
        # "listing failed" rather than as the data problem it is. `created_at`
        # is NOT NULL with a default, so that is theoretical today; the note is
        # here because the call chain under this blanket keeps growing, and the
        # right fix when it stops being theoretical is to narrow the catch, not
        # to improve the message.
        logger.warning(f"[team files] list failed for {team_id}: {e}")
        return {"success": False, "error": str(e)}
