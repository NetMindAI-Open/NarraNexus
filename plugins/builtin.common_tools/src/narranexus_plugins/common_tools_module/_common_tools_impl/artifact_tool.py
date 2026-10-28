"""
@file_name: artifact_tool.py
@author: Bin Liang
@date: 2026-05-08
@description: Register the `register_artifact` MCP tool on the common_tools_module
FastMCP server. The call resolves the per-agent context from the MCP request
headers, opens a fresh DB client, and delegates to ArtifactService.

Pointer model (2026-05-14): the agent writes artifact files into its own
workspace, then calls `register_artifact` with the entry file path. The tool
registers a pointer — it never copies, moves, or writes content.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from narranexus.platform.artifact import ArtifactError, ArtifactService
from narranexus.platform.module_system import (
    caller_event_id_from_request,
    caller_team_id_from_request,
)
from narranexus.platform.utils.db.db_factory import get_db_client


def register(mcp: FastMCP) -> None:
    # FRONTEND COUPLING (display-only since 2026-08-18) — the tool name is
    # still matched by ChatPanel.tsx (`ARTIFACT_TOOL_BASE_NAMES`), but only
    # to anchor the inline badge chip to the tool call that produced an
    # artifact. Live discovery moved to backend-pushed `artifact_changed`
    # events (notify.py outbox → BackgroundRun drain → WS → store), so a
    # rename here costs a missing chip in old transcripts, never a missing
    # tab. Keep the names in sync anyway when renaming.

    @mcp.tool(
        name="register_artifact",
        description=(
            "Show something you built as a rich visual tab next to the chat "
            "(interactive charts, styled HTML pages/apps, formatted reports, "
            "tables, images, PDFs). Artifacts render at full fidelity and look "
            "far better than dumping numbers or ASCII tables into a message.\n"
            "\n"
            "IMPORTANT — files you write are invisible until you register them. "
            "Writing an HTML/JSON/CSV/etc. file into your workspace does NOT "
            "show it to the user. After you've written the file(s), call "
            "register_artifact with the entry file's path to surface it.\n"
            "\n"
            "This tool only registers a POINTER. It does not copy or move your "
            "files — leave them where you wrote them. Deletion is also "
            "pointer-only: removing an artifact removes the tab from the "
            "registry, your workspace files are never touched.\n"
            "\n"
            "Updating an existing artifact: once registered, you can edit the "
            "file(s) in your workspace freely — the registry just holds a "
            "pointer. But the frontend won't reload automatically. To make "
            "the user see your update, call register_artifact AGAIN with "
            "target_artifact_id=<the existing artifact_id>. That second call "
            "is the refresh signal the frontend listens for; it re-fetches "
            "the entry HTML and any sibling assets, so the tab shows your "
            "latest edit. Don't keep creating new tabs for iterations — "
            "re-register the same id. The system-prompt's 'Your registered "
            "artifacts' block tells you which ids are currently live.\n"
            "\n"
            "For a multi-file artifact (HTML page + sibling CSS/JS/JSON/"
            "images), write all the files into a DEDICATED SUBDIRECTORY and "
            "register the entry inside it — the public-raw route serves that "
            "folder, so the entry HTML's relative references (./style.css, "
            "./app.js, ./data.json) resolve. Example: write "
            "./sales_report/index.html plus ./sales_report/style.css, then "
            "register ./sales_report/index.html. Single-file artifacts (one "
            "CSV / Markdown / JSON / image / PDF) can sit directly at the top "
            "level and register just fine; sibling assets simply won't be "
            "served for an entry at the top level.\n"
            "\n"
            "WHICH top level depends on who the artifact is for: your own "
            "workspace for a private artifact, the team shared folder for a "
            "team one. A multi-file "
            "TEAM artifact therefore goes in a subdirectory of the TEAM "
            "folder.\n"
            "\n"
            "entry_path — absolute or workspace-relative path to the entry "
            "file you already wrote.\n"
            "kind — one of these exact values:\n"
            "  text/html   a web page or multi-file app; the entry HTML may "
            "reference sibling assets in its folder\n"
            "  application/vnd.echarts+json   a file containing an ECharts "
            "`option` object as JSON; prefer this for numbers, trends, "
            "comparisons, distributions\n"
            "  text/markdown   a formatted report\n"
            "  text/csv   tabular data\n"
            "  image/png, image/jpeg, application/pdf   a binary file you "
            "generated with another tool\n"
            "title — a short, human-readable tab title.\n"
            "target_artifact_id — pass to update an existing tab in place "
            "(kind must match); omit to create a new tab.\n"
            'scope — leave as "auto". In a team room your artifact goes to '
            "the TEAM workspace, where the whole team (people and teammate "
            "agents) can see it and build on it; in a one-to-one chat it "
            'stays private. Pass "private" only for a scratch draft you do '
            "NOT want your team to see.\n"
            "\n"
            "IN A TEAM ROOM, WRITE THE FILES INTO THE TEAM SHARED FOLDER "
            "(its path is in your team prompt) and register the entry from "
            "there — not into your own workspace. Your workspace is private "
            "to you: your teammates' tools cannot open anything inside it, "
            "so an artifact registered from there is one nobody else can "
            "build on. Registering a team artifact from your own workspace is "
            "refused, and the error names the folder to use.\n"
            "\n"
            "On success returns {artifact_id, url}; the tab is already visible "
            "to the user, so don't repeat the URL in your reply. On failure "
            "returns {error, code} — the error text states the cause (path "
            "outside workspace, file missing, too large); fix the "
            "inputs and call again. A failed register_artifact never blocks "
            "you and is safe to retry."
        ),
    )
    async def register_artifact(
        entry_path: str,
        kind: str,
        title: str,
        agent_id: str,
        user_id: str,
        session_id: Optional[str] = None,
        description: Optional[str] = None,
        target_artifact_id: Optional[str] = None,
        scope: str = "auto",
    ) -> dict:
        """Register-scoped MCP handler for `register_artifact`.

        The LLM-facing contract lives in the `description=` above. This body
        resolves a DB client, decides the artifact's OWNING SCOPE, and
        delegates to `ArtifactService.register`; all validation and path logic
        is there. Every failure path returns a structured `{error, code}` dict.

        Scope resolution — the team comes from the SERVER, the model only gets
        a veto:

          * the turn's team is read from the identity headers, so a model
            cannot place an artifact into a team by naming one (`agent_id` is
            already a model-filled parameter — trusting arguments here would
            let a private chat write into a team's workspace);
          * `scope="private"` is the one lever the model has, and it can only
            NARROW: it opts a draft out of the team it is actually in;
          * anything else, including the default and any value the model
            invents, follows the turn. Defaulting to the turn is what makes
            this safe under a weak model: the common case needs no decision at
            all, and an unrecognised value degrades to the correct behaviour
            rather than to a private artifact nobody can find.

        `scope` is a plain `str` with a default rather than `Optional[str]`:
        FastMCP renders Optional as `anyOf:[X,null]`, which strict-schema
        providers reject with a request-level 400 — the whole request fails,
        not just this call.
        """
        try:
            db = await get_db_client()
            service = ArtifactService(db)
            team_id = None if str(scope).strip().lower() == "private" else caller_team_id_from_request()
            result = await service.register(
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                kind=kind,  # type: ignore[arg-type]
                entry_path=entry_path,
                title=title,
                description=description,
                target_artifact_id=target_artifact_id,
                team_id=team_id,
                # Server-side, like the team: the turn that made a change is a
                # fact the platform holds, not something to ask the model for.
                event_id=caller_event_id_from_request(),
            )
            return result.model_dump(mode="json")
        except ArtifactError as e:
            # Expected, structured rejection (bad kind, path escape, too large, ...).
            # The message is already actionable; hand it straight to the LLM.
            logger.warning(f"register_artifact rejected: {e}")
            return {"error": str(e), "code": e.code}
        except Exception as e:  # noqa: BLE001
            # Unexpected failure (DB hiccup, disk error, ...). NEVER let it
            # propagate as an unhandled MCP exception — that surfaces to the
            # agent as an opaque tool crash and can stall the loop. Return a
            # structured, retryable error so the agent reads the cause and
            # simply calls the tool again.
            logger.exception(f"register_artifact failed unexpectedly: {e}")
            return {
                "error": f"register_artifact failed unexpectedly: {e}. "
                f"This is likely transient — you can call the tool again.",
                "code": 500,
            }

    @mcp.tool(
        name="open_url",
        description=(
            "Open a web page as a tab next to the chat, so the user (and you) "
            "can see it. Use this when you found a relevant web page — a "
            "dashboard, a doc, a live report — and want to surface it, instead "
            "of just pasting the link in a message.\n"
            "\n"
            "The system probes the page: if it allows embedding it shows "
            "inline; if it refuses (many big sites do), the tab shows a card "
            "with an 'open in new window' button — either way the tab is "
            "created. Only public http(s) URLs are accepted; internal/"
            "loopback addresses are rejected.\n"
            "\n"
            "url — the full http(s) URL. title — a short tab title (optional; "
            "defaults to the URL).\n"
            "On success returns {artifact_id, url}; the tab is already visible, "
            "so don't repeat the link. On failure returns {error, code}."
        ),
    )
    async def open_url(
        url: str,
        agent_id: str,
        user_id: str,
        title: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> dict:
        """Register-scoped MCP handler for `open_url`. Delegates to
        `ArtifactService.open_url`; every failure path returns {error, code}."""
        try:
            db = await get_db_client()
            service = ArtifactService(db)
            result = await service.open_url(
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                url=url,
                title=title,
            )
            return result.model_dump(mode="json")
        except ArtifactError as e:
            logger.warning(f"open_url rejected: {e}")
            return {"error": str(e), "code": e.code}
        except Exception as e:  # noqa: BLE001
            logger.exception(f"open_url failed unexpectedly: {e}")
            return {
                "error": f"open_url failed unexpectedly: {e}. This is likely transient — you can call the tool again.",
                "code": 500,
            }


# ── list_artifacts (2026-08-18, spec artifact-events §4) ─────────────────────

#: Page size for list_artifacts. Big enough that page 1 answers almost every
#: real inventory question in one call, small enough to keep a pathological
#: hoard from flooding one tool result.
LIST_ARTIFACTS_PAGE_SIZE = 50


async def list_artifacts_impl(
    db,
    *,
    agent_id: str,
    user_id: str,
    kind: str = "",
    team_id: str = "",
    title_contains: str = "",
    page: int = 1,
) -> str:
    """Plain-function body of the list_artifacts tool (tested directly).

    The visible surface is `list_for_agent_context(agent_id)` — own pinned
    artifacts plus every team this agent belongs to, membership derived
    SERVER-SIDE inside the repository query. No parameter can widen it:
    `kind` / `team_id` / `title_contains` only narrow, so a team_id the
    agent is not a member of filters an already-authorized set down to
    nothing (safe by construction, no extra check needed).

    Future audience isolation (todo artifact_visibility_audience_blind):
    when turn-audience filtering lands, it will be applied HERE from the
    server-held identity headers (`caller_team_id_from_request`), exactly
    like register_artifact's team scoping — never from model-filled
    parameters. The tool signature will not need to change.
    """
    from narranexus_plugins.common_tools_module._common_tools_impl.artifact_lines import (
        format_artifact_lines,
    )
    from narranexus.platform.repository.artifact_repository import ArtifactRepository

    repo = ArtifactRepository(db)
    # Filters and paging live in SQL (review #334 I10): the page size must
    # mean something to the DB, not be a Python slice over a full pull.
    # title matching moved to LIKE — case-insensitive per DB collation (ASCII
    # ci on SQLite), a hair narrower than the old .lower() for non-ASCII.
    total = await repo.count_agent_context_filtered(
        agent_id, kind=kind, team_id=team_id, title_contains=title_contains
    )
    pages = max(1, (total + LIST_ARTIFACTS_PAGE_SIZE - 1) // LIST_ARTIFACTS_PAGE_SIZE)
    page = max(1, min(int(page or 1), pages))
    window = await repo.search_agent_context(
        agent_id,
        kind=kind,
        team_id=team_id,
        title_contains=title_contains,
        limit=LIST_ARTIFACTS_PAGE_SIZE,
        offset=(page - 1) * LIST_ARTIFACTS_PAGE_SIZE,
    )

    filters = []
    if kind:
        filters.append(f"kind={kind}")
    if team_id:
        filters.append(f"team={team_id}")
    if title_contains:
        filters.append(f"title~{title_contains!r}")
    filter_note = f" (filtered: {', '.join(filters)})" if filters else ""

    header = (
        f"Your artifacts: {total} match{filter_note} — "
        f"page {page}/{pages}, newest first"
    )
    if not window:
        return f"{header}\n(no artifacts match — drop a filter or check the spelling)"

    lines = [header]
    lines.extend(
        line for _aid, line in format_artifact_lines(window, agent_id=agent_id, user_id=user_id)
    )
    if pages > page:
        lines.append(f"(call again with page={page + 1} for the next {LIST_ARTIFACTS_PAGE_SIZE})")
    return "\n".join(lines)


def register_list_artifacts(mcp: FastMCP) -> None:
    """Register the read-only inventory tool. Split from register() so the
    server factory wires both with one import site."""

    @mcp.tool(
        name="list_artifacts",
        description=(
            "List every artifact you can reach — your own plus those of every "
            "team you belong to — newest first, 50 per page. Your per-turn "
            "context only shows the 20 most recently updated, so CALL THIS "
            "before concluding an artifact does not exist or creating a "
            "replacement for something that might already be registered.\n"
            "\n"
            "Optional narrowing: kind (exact, e.g. 'text/html'), team_id "
            "(only that team's), title_contains (case-insensitive substring), "
            "page (default 1). Filters only narrow — you cannot see another "
            "agent's private artifacts or teams you are not in.\n"
            "\n"
            "Read-only: to change something, use register_artifact with "
            "target_artifact_id."
        ),
    )
    async def list_artifacts(
        agent_id: str,
        user_id: str,
        kind: str = "",
        team_id: str = "",
        title_contains: str = "",
        page: int = 1,
    ) -> str:
        # str defaults instead of Optional[...]: FastMCP renders Optional as
        # anyOf:[X,null], which strict-schema providers reject request-wide
        # (same lesson as register_artifact's `scope`).
        try:
            db = await get_db_client()
            return await list_artifacts_impl(
                db,
                agent_id=agent_id,
                user_id=user_id,
                kind=kind,
                team_id=team_id,
                title_contains=title_contains,
                page=page,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception(f"list_artifacts failed unexpectedly: {e}")
            return (
                f"[tool_error] list_artifacts failed: {e}. Likely transient — "
                "you can call the tool again."
            )
