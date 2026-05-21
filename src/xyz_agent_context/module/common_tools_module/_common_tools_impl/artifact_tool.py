"""
@file_name: artifact_tool.py
@author: Bin Liang
@date: 2026-05-08
@description: Register the `register_artifact` MCP tool on the common_tools_module
FastMCP server. The call resolves the per-agent context from the MCP request
headers, opens a fresh DB client, and delegates to artifact_runner.

Pointer model (2026-05-14): the agent writes artifact files into its own
workspace, then calls `register_artifact` with the entry file path. The tool
registers a pointer — it never copies, moves, or writes content.
"""

from __future__ import annotations

from typing import Optional

from loguru import logger
from mcp.server.fastmcp import FastMCP

from xyz_agent_context.module.common_tools_module._common_tools_impl import artifact_runner
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.utils.db_factory import get_db_client


def register(mcp: FastMCP) -> None:
    # ⚠️ FRONTEND COUPLING — the tool name is matched by the UI.
    #
    # `register_artifact` calls are recognised in the agent event stream by
    # `frontend/src/components/chat/ChatPanel.tsx` (`isArtifactToolName` /
    # `ARTIFACT_TOOL_BASE_NAMES`). The frontend matches the BARE name as a
    # suffix — it tolerates the fully-qualified
    # `mcp__common_tools_module__register_artifact` form the stream carries.
    #
    # That match drives the artifact panel's *live* discovery (the tab
    # appearing during/right after a run). If you rename this tool, you MUST
    # update `ARTIFACT_TOOL_BASE_NAMES` in ChatPanel.tsx in the same change —
    # otherwise registered artifacts silently stop showing up until an
    # unrelated reload (e.g. switching agents).

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
            "images), write all the files into a dedicated subdirectory of "
            "your workspace and register the entry inside it — the public-raw "
            "route serves that folder, so the entry HTML's relative "
            "references (./style.css, ./app.js, ./data.json) resolve. "
            "Example: write ./sales_report/index.html plus "
            "./sales_report/style.css, then register ./sales_report/index.html. "
            "Single-file artifacts (one CSV / Markdown / JSON / image / PDF) "
            "can live anywhere inside your workspace — including the workspace "
            "root — and register just fine; sibling assets simply won't be "
            "served when the entry is at the workspace root.\n"
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
    ) -> dict:
        """Register-scoped MCP handler for `register_artifact`.

        The LLM-facing contract lives in the `description=` above. This body
        just resolves a DB client and delegates to
        `artifact_runner.register_artifact`; all validation and path logic
        is there. Every failure path returns a structured
        `{error, code}` dict.
        """
        try:
            db = await get_db_client()
            repo = ArtifactRepository(db)
            result = await artifact_runner.register_artifact(
                repo=repo,
                agent_id=agent_id,
                user_id=user_id,
                session_id=session_id,
                kind=kind,  # type: ignore[arg-type]
                entry_path=entry_path,
                title=title,
                description=description,
                target_artifact_id=target_artifact_id,
            )
            return result.model_dump(mode="json")
        except artifact_runner.ArtifactError as e:
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
