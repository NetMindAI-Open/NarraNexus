"""
@file_name: common_tools_module.py
@author: Bin Liang
@date: 2026-04-17
@description: CommonToolsModule — generic utility tools for every agent

This module covers tools that are useful to every agent regardless of domain:
- `web_search`: DuckDuckGo search (replaces Anthropic's built-in web_search for
  non-Anthropic providers like NetMind that do not ship one)

Design choices:
- module_type="capability": always loaded, no instance record, no decision LLM
- Stateless MCP tools: the tools accept plain arguments; no per-agent state
- Room to grow: extra utilities (fetch_url, read_pdf, ...) live under the same
  MCP server to keep the tool-count moderate
"""

import asyncio
from typing import Any, List, Optional

from loguru import logger

from narranexus.platform.artifact import (
    ArtifactPathEscape,
    ArtifactService,
    office_lock_present,
)
from narranexus.platform.module_system.base import XYZBaseModule, mcp_server_url
from narranexus.platform.repository.team_workspace_repository import (
    ArtifactHistoryRepository,
)
from narranexus.platform.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)
from narranexus.platform.settings import settings
from narranexus.platform.utils import DatabaseClient


#: How many artifacts the "Your registered artifacts" block may carry.
#:
#: The block used to list every pinned artifact with no limit, on every
#: turn. Artifacts have no quota, so a prolific agent grew its own system
#: prompt without bound — a cost paid on each turn forever, for entries it
#: had long stopped touching.
#:
#: Capping is safe because the block is a convenience, not the source of
#: truth: the full set stays reachable through the artifact tooling. Paired
#: with `updated_at DESC` the cap is also self-correcting — the update path
#: re-registers, refreshing the timestamp, so whatever is being iterated on
#: stays listed and only cold entries fall off.
ARTIFACT_STATE_BLOCK_LIMIT = 20


COMMON_TOOLS_INSTRUCTIONS = """\
#### Dates and Time Arithmetic

You have `resolve_relative_date(unit, offset, weekday="", reference="")` and
`compare_dates(date_a, date_b="")`. Both resolve in the user's timezone and
both report `is_past` / `is_today` / `days_from_today` alongside the date.

**Do not work dates out in your head.** This is not a style preference — it
is the single most common way an agent loses a user's trust, because a wrong
date sounds exactly as confident as a right one. Two rules:

1. Any relative expression — "next Friday" / "下周五" / "tomorrow" / "后天" /
   "in three weeks" / "end of next month" — goes through
   `resolve_relative_date`. You split the phrase into (unit, offset,
   weekday); the tool does the calendar arithmetic. When the phrase came
   from an older message, pass that message's date as `reference` — the
   timeline lines carry it.
2. Before you assert that something has happened, is happening today, or is
   still ahead — "今天是…" / "that's tomorrow" / "that already passed" —
   confirm with `compare_dates` and quote what it returns. A date you read
   correctly out of a record can still be compared against "now" wrongly,
   and that is the mistake users actually notice.

If a resolved date is ambiguous in the user's phrasing (English "next
Friday" genuinely is), state the date and the week you landed on rather than
picking one silently — the reply carries `week_start` / `week_end` for this.

#### Generic Web Search

You have access to `web_search(queries: list[str], max_results_per_query: int = 5)`.
Use it whenever you need up-to-date information that is not in your context:

- Each entry in `queries` can be a **natural-language question**
  (e.g. "What is the latest iPhone 17 release date?") **or a keyword string**
  (e.g. "python asyncio gather exceptions"). Pick whichever matches how the
  information is likely written on the web.
- Pass **multiple queries at once** when you want to cover different angles
  (e.g. official docs + user discussion + recent news). They run in parallel.
- Results come back as title + URL + snippet grouped by query. If you need the
  full page, follow up with a fetch tool; do not assume the snippet is the
  whole answer.
- The search engine is DuckDuckGo — no API key required, but it is
  rate-limited, so avoid hammering it with dozens of queries in a row.

#### Reading User-Uploaded Files

When the user attaches a file (image, PDF, document, code, data — anything),
two things happen:

1. The conversation message itself carries a marker like
   `[User uploaded file: name="...", path="/abs/path/...", mime="...", kind="..." — use Read tool to view]`
   (every value is a JSON string; for an ordinary path pass the text inside the
   `path` quotes to Read, and decode any escapes such as `\\"`, `\\\\`, `\\t`, `\\n` first).
2. If the upload happened on the *current* turn, this instruction gains a
   `## Files attached to the current message` block listing the same paths.

To act on an attachment, call the built-in `Read` tool with the absolute
path. Read is multimodal — its return shape depends on the file's type:

- **Text / code / data files** (`.md`, `.txt`, `.py`, `.ts`, `.json`,
  `.csv`, `.yaml`, ...) — returned as line-numbered text content. Every
  model can read this, regardless of vision support.
- **PDFs and rich documents** — returned as document content blocks
  (extracted text + page renders). Text extraction works on every model;
  page-level visual interpretation requires a vision-capable model.
- **Images** (`.jpg`, `.png`, `.gif`, `.webp`) — returned as a visual
  content block. Vision-capable models perceive the picture directly;
  text-only models receive opaque bytes and CANNOT see the image.

You do NOT need any custom `load_image` / `read_attachment` tool; Read
covers everything.

Rules:
- **Read each attachment AT MOST ONCE per turn.** Reading the same file
  again returns the exact same bytes — repeating it never helps and
  burns tokens.

- **Vision self-check applies ONLY to image attachments.** For text /
  code / data / document files, Read the file and proceed normally —
  there is no self-check, no model-capability concern, just answer the
  user using the file's contents. For images specifically, before you
  say a single word about what is in the picture, you MUST first write
  a short self-check listing THREE concrete, verifiable visual specifics:
    1. The dominant color of the top-left quadrant in plain words
       (e.g. "warm orange", "near-black", "pale blue").
    2. Any text visible in the image, transcribed verbatim — or
       "no text visible".
    3. A count of distinct foreground objects with brief positions
       (e.g. "2 people, both in the lower half").
  If you cannot produce any of these three with grounded specifics —
  if your candidate answer feels like a guess based on the filename or
  the conversation rather than something you literally see — then your
  underlying model does NOT support vision. The image content either
  reached you as opaque bytes (a long base64-looking string in the Read
  result) or was stripped before reaching you. This is a model
  capability limit, not a tool failure; calling Read again or scanning
  with Bash / Glob / Task will not change the outcome.
  In that case, STOP. Do NOT describe the image. Send the user a single
  message that:
    1. Confirms the file uploaded successfully.
    2. Plainly states that the current model is text-only and cannot
       read images — non-image attachments would still work.
    3. Suggests opening the agent's Settings and switching to a
       vision-capable model — any Claude family model (Opus / Sonnet /
       Haiku) supports image input.
  Do not pretend to see the picture; do not invent contents.

- If the marker says `path=<unavailable>`, the file is no longer on disk.
  Acknowledge the upload but tell the user you cannot view it; do not
  fabricate content.
- Do NOT modify or delete user-uploaded files unless the user explicitly
  asks you to.

#### Visual Artifacts — write files, then register

You can surface rich visual artifacts in a dedicated tab right next to the
chat: interactive charts, styled HTML pages and apps (entry HTML +
sibling assets), formatted reports and tables, images, PDFs. They render at
full fidelity — far better than dumping numbers or ASCII tables into a chat
message.

Treat artifacts as a first-class part of your response, not an extra step.
**Default to an HTML artifact whenever the information is complex, long, or
structured.** If you could express the answer as an HTML page — a report, a
dashboard, a comparison, a document, a multi-section write-up, anything with
structure — it is almost always clearer rendered as an HTML artifact than as
a wall of chat text. When in doubt, write the HTML and register it. Build and
register it directly as part of doing the task — no need to announce it
first.

How it works:

1. Write the artifact file(s) somewhere in your workspace. Files are
   invisible to the user until you register them.
2. Call `register_artifact` with the entry file's path — it returns
   `{{artifact_id, url}}` and the tab appears.

`register_artifact` only registers a **pointer**. It does not move or copy
your files — keep them in place. Deleting an artifact also only removes the
tab from the registry; your workspace files stay where you wrote them and
are yours to clean up (or keep) via the workspace section.

**Updating an existing artifact.** Once registered, you can edit the file(s)
in your workspace freely — the registry just holds a pointer, so the bytes
the user sees are whatever is on disk at fetch time. **But the frontend
won't reload automatically.** To make the user see your update, call
`register_artifact` **again** with `target_artifact_id=<the existing
artifact_id>` (other args optional — same path, same kind, same title is
fine). That second call is the refresh signal the frontend listens for: it
re-fetches the entry HTML and any sibling assets, so the tab shows your
latest edit. Don't keep registering new tabs for iterations — re-register
the same id.

**Sibling-asset capability.** When the entry lives in a subdirectory, the
public-raw route serves that whole folder — so an entry HTML can reference
siblings with relative paths (`./style.css`, `./data.json`, images) and they
all load. So for multi-file artifacts (HTML page/app with assets), write the
files into a dedicated subdirectory and register the entry inside it.
Single-file artifacts (one CSV / Markdown / JSON / image / PDF) can sit
anywhere, including the workspace root.

You can always check your current artifact registry in the "Your registered
artifacts" block that's injected each turn — it tells you the ids, kinds,
titles and workspace paths of everything you have live right now, so you
know what to `target_artifact_id` at vs. when to create a new tab.

The tool description on `register_artifact` carries the exact `kind` values
and parameters — follow that.

**Where artifacts are visible.** The artifact tab lives in the owner's web /
desktop chat UI. Someone interacting with you through an IM channel (Lark /
Slack / Telegram) does NOT see artifacts — they only see what you send back
on their channel. So an artifact is always worth building for the owner, but
it never replaces a reply on an IM channel: if this turn's audience is an IM
sender, still answer them in the channel message itself (and register the
artifact for the owner only if it's useful to them).

Guidance:
- For numbers, trends, comparisons or distributions, default to an ECharts
  artifact (a JSON file containing the ECharts `option` object) — not a
  markdown table, and never an ASCII table in chat.
- Don't paste the artifact URL into your reply — the UI already shows the tab.
- If `register_artifact` returns an error, the error text states the cause
  (path outside workspace, file missing, too large); fix the inputs
  and call again — a failed call never blocks you and is safe to retry.
"""


class CommonToolsModule(XYZBaseModule):
    """Always-on capability module exposing generic tools (web_search, ...)."""

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.instructions = COMMON_TOOLS_INSTRUCTIONS

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="CommonToolsModule",
            always_load=True,
            priority=50,
            enabled=True,
            description="Generic utility tools available to every agent (web_search, ...)",
            module_type="capability",
        )

    async def gather(self, ctx_data: ContextData) -> ContextData:
        return ctx_data

    async def _volatile_sections(self, ctx_data: ContextData) -> List[str]:
        """The two per-turn dynamic blocks (R4: relocated, never dropped):

        1. *Attached files* for this turn — built from
           `ctx_data.extra_data["attachments"]`, populated by the trigger
           layer (WebSocket / Lark / Job / ...). Resolution lives in
           `utils/attachment_storage`. (The attachment double-rendering —
           this block plus the Read marker on the user message — is kept
           as-is; with the relocation flag on both live on the message side.)
        2. *Registered artifacts* the agent has live RIGHT NOW — pulled
           fresh from `ArtifactRepository.list_for_agent_context(agent_id)` so the
           agent always sees the current set (id, kind, title, workspace
           path) and can decide whether to re-register an existing one
           vs. create a new one. This is the data-gathering surface for
           the artifact registry. Changed mid-session by register_artifact,
           hence per-turn volatile.
        """
        from narranexus.platform.utils.attachment_storage import (
            format_attachments_for_system_prompt,
        )

        sections: List[str] = []

        # ── attachments for this turn ────────────────────────────────────
        attachments = []
        if ctx_data.extra_data:
            raw = ctx_data.extra_data.get("attachments")
            if isinstance(raw, list):
                attachments = raw
        if attachments:
            appendix = format_attachments_for_system_prompt(
                attachments,
                agent_id=self.agent_id,
                user_id=self.user_id or "",
            )
            if appendix:
                sections.append(appendix)

        # ── live artifact registry ───────────────────────────────────────
        artifact_block = await self._render_artifact_state_block()
        if artifact_block:
            sections.append(artifact_block)

        return sections

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        """Static base instruction, plus (flag OFF only) the two dynamic
        appendices.

        With the R4 relocation flag ON the output is the constant
        COMMON_TOOLS_INSTRUCTIONS (byte-stable across turns) and the
        appendices travel via contribute_turn_context(); flag OFF keeps the legacy
        single-block rendering, functionally equivalent to pre-R4.
        """
        sections = [self.instructions]
        if not settings.prompt_turn_context_relocation_enabled:
            sections.extend(await self._volatile_sections(ctx_data))
        return "\n\n".join(sections)

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        """Per-turn volatile span: the attachments appendix and the live
        artifact registry (each block carries its own stable heading)."""
        sections = await self._volatile_sections(ctx_data)
        if not sections:
            return ""
        return "\n\n".join(sections)

    async def _render_artifact_state_block(self) -> str:
        """Build the 'Your registered artifacts' appendix for this turn.

        Lists what this agent should be aware of: its own pinned artifacts
        plus those of every team it belongs to. Each
        line is `art_id [kind] "title" → workspace/relative/path`. Paths
        are made workspace-relative (the `{agent_id}_{user_id}/` prefix
        is stripped from the DB-stored path) because that's the form the
        agent's Write/Edit tools see.

        Returns an empty string on DB errors (the rest of the instruction
        is still useful; this block is best-effort).
        """
        if not self.db:
            return ""
        try:
            from narranexus.platform.repository.artifact_repository import (
                ArtifactRepository,
            )
            repo = ArtifactRepository(self.db)
            # Union, not `list_pinned`: the agent must also see the artifacts
            # of every team it belongs to, or it cannot pick up a teammate's
            # work — the block is where it learns those exist at all.
            artifacts = await repo.list_for_agent_context(
                self.agent_id, limit=ARTIFACT_STATE_BLOCK_LIMIT
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"artifact-state block: list_for_agent_context failed: {e}")
            return ""

        header = "#### Your registered artifacts (live)"
        if not artifacts:
            return (
                f"{header}\n"
                "(none registered yet — call `register_artifact` to surface "
                "a file as a tab the user can see)"
            )

        from narranexus_plugins.common_tools_module._common_tools_impl.artifact_lines import (
            format_artifact_lines,
        )

        # Trigger point T-A of external-edit safety (spec B §2.3): the block
        # is rendered right before the agent starts working, so this is the
        # last moment to notice that a file moved past its last commit point.
        # Detection commits the change (hash refresh + history + event);
        # markers derive from the LAST history action so an agent
        # re-registration clears them. Best-effort: a failed check must not
        # cost the block.
        markers: dict = {}
        try:
            service = ArtifactService(self.db)
            # Bounded concurrency, not a serial await chain: each check may
            # stat + (rarely) hash; four in flight keeps a 20-line block fast
            # without stampeding the DB pool (review #334 I12). Failures are
            # per-artifact best-effort — one bad row must not kill the pass.
            sem = asyncio.Semaphore(4)

            async def _check(a):
                async with sem:
                    try:
                        await service.refresh_external_state(a)
                    except Exception as e:  # noqa: BLE001 — detector is best-effort
                        logger.warning(
                            f"artifact-state block: freshness check failed for {a.artifact_id}: {e}"
                        )

            await asyncio.gather(*(_check(a) for a in artifacts))
            latest = await ArtifactHistoryRepository(self.db).latest_actions(
                [a.artifact_id for a in artifacts]
            )
            for a in artifacts:
                notes = []
                action = latest.get(a.artifact_id)
                if action == "external_edited":
                    notes.append(
                        "EXTERNALLY MODIFIED since your last update; "
                        "re-read the file before editing"
                    )
                elif action == "user_edited":
                    notes.append(
                        "modified by the user since your last update; "
                        "re-read before editing"
                    )
                if a.kind == "application/vnd.officecli-live" and a.file_path:
                    # Probe next to the OWNER-CONFINED entry only: a pointer
                    # (or a folder swapped for a link) aiming at another
                    # tenant's tree gets no note rather than a peek there.
                    try:
                        abs_entry = service.resolve_owned_entry(a)
                    except ArtifactPathEscape:
                        abs_entry = None
                    if abs_entry is not None and office_lock_present(abs_entry):
                        notes.append(
                            "currently open in a desktop Office app; its saves "
                            "may race yours while the lock is held"
                        )
                if notes:
                    markers[a.artifact_id] = " — " + "; ".join(notes)
        except Exception as e:  # noqa: BLE001 — markers degrade, the block survives
            logger.warning(f"artifact-state block: freshness pass failed: {e}")

        lines = [header]
        for aid, line in format_artifact_lines(
            artifacts, agent_id=self.agent_id, user_id=self.user_id or ""
        ):
            lines.append(line + markers.get(aid, ""))
        # Truthful footer: the cap must never be silent. Past the limit the
        # agent is told it is looking at a window and which tool lists the
        # rest — otherwise older artifacts read as nonexistent and get
        # recreated (spec artifact-events §4). COUNT is a separate cheap
        # query; on failure we degrade to no footer rather than no block.
        try:
            total = await repo.count_for_agent_context(self.agent_id)
        except Exception:  # noqa: BLE001
            total = len(artifacts)
        if total > len(artifacts):
            lines.append(
                f"(showing the {len(artifacts)} most recently updated of "
                f"{total} — call `list_artifacts` to see the rest, filter "
                "by kind/team, or search titles)"
            )
        lines.append("")
        lines.append(
            "Paths above are relative to your workspace unless they start "
            "with `/` — an absolute one belongs to a teammate or to your "
            "team's shared folder, so open it exactly as written rather than "
            "treating it as relative to your own files.\n"
            "To update what the user sees: edit the file(s) in place, then "
            "call `register_artifact` again with "
            "`target_artifact_id=<that artifact's id>` — the re-registration "
            "call itself is the refresh signal the frontend listens for. "
            "To change the title or repoint at a different entry file, pass "
            "the new value(s) alongside `target_artifact_id`."
        )
        return "\n".join(lines)

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="common_tools_module",
            server_url=mcp_server_url("common_tools_module"),
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        from narranexus_plugins.common_tools_module._common_tools_mcp_tools import (
            create_common_tools_mcp_server,
        )
        return create_common_tools_mcp_server()
