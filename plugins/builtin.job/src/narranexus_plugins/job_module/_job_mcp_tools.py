"""
@file_name: _job_mcp_tools.py
@author: NetMind.AI
@date: 2025-11-25
@description: JobModule MCP Server tool definitions

Separates MCP tool registration logic from the JobModule main class,
keeping JobModule focused on Hook and core business logic.

Tools:
- job_create: Create a background job
- job_retrieval_semantic: Semantic search for jobs
- job_retrieval_by_id: Query job by ID
- job_retrieval_by_keywords: Keyword search for jobs
- job_update: Update job properties
- job_pause: Pause a job
- job_cancel: Cancel a job
"""

from typing import Annotated, Optional, List, Any, NotRequired, TypedDict

from mcp.server.fastmcp import FastMCP
from pydantic import Field, TypeAdapter, WithJsonSchema

from loguru import logger

from narranexus.platform.module_system.data_access import get_agent_data_store
from narranexus.platform.schema.job_schema import JobOrigin


async def _caller_job_origin() -> tuple[Optional[str], Optional[str]]:
    """``(origin_source, origin_channel_id)`` for the turn calling job_create.

    Answers one question: is this turn happening in a TEAM ROOM, and which one.
    A room-origin job reports back into that room; everything else keeps the
    owner's chat, which is the surface that always exists.

    Derived from the injected team id rather than a new bearer field. The
    bearer's field list is positional and frozen — appending is legal but costs
    a protocol change and every reader — while a team room is deterministically
    the group channel whose ``created_by`` is the ``team_<id>`` marker, so the
    fact is already on the wire.

    Peer DMs are deliberately NOT an origin: an agent-to-agent channel has no
    human reader, so a report delivered there is a report nobody sees.

    Fails to ``(None, None)`` — a job that reports to the owner is the old
    behaviour, and the old behaviour is never the wrong answer to "we could not
    work out where this came from".
    """
    try:
        from narranexus.platform.module_system import caller_team_id_from_request
        from narranexus.platform.message_bus.team_rooms import primary_room_of
        from narranexus.platform.utils.db.db_factory import get_db_client

        team_id = caller_team_id_from_request()
        if not team_id:
            # A private-chat or peer-DM turn. The overwhelmingly common case
            # and entirely normal, so it must stay silent — a warning here
            # would fire on every owner-chat job and train everyone to ignore
            # the log line that matters below.
            return (None, None)
        # One implementation of "where is this team's room" (see team_rooms):
        # this resolver used to carry its own copy, which had already drifted
        # from its sibling in the work-board tools.
        channel_id = await primary_room_of(await get_db_client(), team_id)
        # A team whose room does not exist yet resolves to nothing rather than
        # to a source with no channel: recording half of the pair would send
        # execution down the room branch with nowhere to post.
        if not channel_id:
            logger.warning(
                f"[job.create] turn carries team={team_id} but its room could "
                f"not be resolved; this job will report to the owner instead"
            )
            return (None, None)
        return (JobOrigin.MESSAGE_BUS, channel_id)
    except Exception as e:  # noqa: BLE001 — see docstring
        # WARNING, not DEBUG. This except is what a future "the mcp container
        # holds no DB credentials" migration would start hitting, and the
        # symptom is silent: room origin disappears and jobs quietly fall back
        # to the owner's private chat — i.e. the exact bug this feature fixed,
        # returning with no trace. Whoever flips that switch needs to see it.
        logger.warning(f"[job.create] could not resolve origin: {e}")
        return (None, None)


class TriggerConfigArg(TypedDict):
    """Trigger configuration with an IANA timezone; fields vary by job type."""

    timezone: str
    run_at: NotRequired[str]
    cron: NotRequired[str]
    interval_seconds: NotRequired[int]
    end_condition: NotRequired[str]
    max_iterations: NotRequired[int]
    # Scheduling horizon for recurring ("scheduled"/"ongoing") jobs: naive
    # local ISO 8601 (no "Z"/offset — same convention as run_at, interpreted
    # in `timezone`). The platform completes the job once its next fire would
    # land past it. Ignored for one_off.
    end_at: NotRequired[str]


# FastMCP asks pydantic for the containing function's schema. A named
# TypedDict is normally emitted through $defs/$ref, and Optional adds an
# anyOf-null wrapper. Some tool-schema providers reject both constructs.
# TypeAdapter keeps the inline public shape derived from the TypedDict itself.
_TRIGGER_CONFIG_JSON_SCHEMA = TypeAdapter(TriggerConfigArg).json_schema()


def _remove_schema_default(schema: dict[str, Any]) -> None:
    """Keep the published object schema consistent with its non-null type."""
    schema.pop("default", None)


# Runtime input is intentionally a plain dict so canonical TriggerConfig
# validation runs inside each tool and returns the same structured error shape
# as other tool errors. The published schema remains the strict TypedDict shape.
TriggerConfigInput = Annotated[
    dict[str, Any],
    WithJsonSchema(_TRIGGER_CONFIG_JSON_SCHEMA),
]
OptionalTriggerConfigInput = Annotated[
    Optional[dict[str, Any]],
    WithJsonSchema(_TRIGGER_CONFIG_JSON_SCHEMA),
    Field(json_schema_extra=_remove_schema_default),
]


def create_job_mcp_server() -> FastMCP:
    """
    Create a JobModule MCP Server instance

    Every tool routes DB access through the AgentDataStore seam
    (get_agent_data_store) — DirectStore locally, HttpStore in cloud — so the
    mcp container needs no DB credentials. No get_db_client_fn is threaded in.

    Args:

    Returns:
        FastMCP instance with all tools configured
    """
    mcp = FastMCP("job_module")

    # -----------------------------------------------------------------
    # Tool: job_create
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_create(
        agent_id: str,
        user_id: str,
        title: str,
        description: str,
        job_type: str,
        trigger_config: TriggerConfigInput,
        payload: str,
        notification_method: str = "direct",
        task_key: Optional[str] = None,
        depends_on_job_ids: Optional[List[str]] = None,
        related_entity_id: Optional[str] = None,
        narrative_id: Optional[str] = None,
        confirm_new: bool = False
    ) -> dict:
        """
        Create a background Job. IDEMPOTENCY: first check "Jobs I Just Created"
        in my instructions; call ONLY if no matching job already exists.

        Args:
            agent_id: Owning Agent ID
            user_id: Requesting User ID
            title: Short title
            description: Detailed description
            job_type: "one_off" (run once) | "scheduled" (repeat on
                cron/interval) | "ongoing" (repeat until end_condition met;
                use for persistent follow-up goals)
            trigger_config: Shape per job_type; EVERY shape REQUIRES "timezone"
                (IANA name from User Temporal Context). "cron" and "run_at" are
                WALL-CLOCK times IN that timezone — never convert the user's
                time to UTC: "9am Beijing" is {"cron": "0 9 * * *", "timezone":
                "Asia/Shanghai"}, NOT "0 1 * * *".
                - one_off: {"run_at": "2026-01-20T09:00:00", "timezone": "Asia/Shanghai"}
                  run_at MUST be naive ISO 8601 — no "Z"/offset suffix.
                - scheduled: {"cron": "0 8 * * *", "timezone": ...} OR
                  {"interval_seconds": 3600, "timezone": ...}; optional
                  "end_at" (naive local ISO 8601, like run_at) = run until
                  that date, then the PLATFORM completes the job — use it
                  whenever the user gives a bounded duration ("for two
                  weeks", "until Friday") instead of asking yourself to
                  remember to stop.
                - ongoing: {"interval_seconds": 86400, "end_condition": "...",
                  "timezone": ...}; also accepts optional "end_at" (same
                  platform-enforced horizon as scheduled).
            payload: Instruction executed when the job runs
            notification_method: delivery method (use default)
            task_key: Optional dependency identifier
            depends_on_job_ids: instance_ids ("job_xxxxxxxx") to wait for —
                NOT DB job_id
            related_entity_id: Target user ID. RULE: report-back-to-requester
                job → requester's user_id; job acting ON another user → that
                user's ID. Decides whose context loads at execution.
            narrative_id: Narrative to load as conversation context at execution
            confirm_new: Pass true ONLY after the result asked
                needs_confirmation AND the user confirmed they want a new job
                despite the similar existing one. Never pre-set it.

        Returns: dict(success, job_id, instance_id, message). On a
            similar-title hit: dict(success=False, needs_confirmation=True,
            similar_job) — tell the user, ask which they meant.

        Example:
            job_create(agent_id="agent_1", user_id="user_m", title="Report",
                description="...", job_type="scheduled", payload="...",
                trigger_config={"cron": "0 18 * * *", "timezone": "Asia/Shanghai"})

        Common errors: missing "timezone"; run_at with "Z"/offset; "scheduled"
        with end_condition (use "ongoing"); DB job_id in depends_on_job_ids.
        """
        # Routes through the AgentDataStore seam (DirectStore local / HttpStore
        # cloud). create_job_from_args owns the LLM-context setup + similar-title
        # embedding check + W1 structured-error handling, so it runs in whichever
        # process holds the DB (mcp locally, backend in cloud).
        # WHERE this turn is running, taken from the server-injected identity
        # and never from a tool parameter: a model asked "which room are you
        # in" can only guess, and a guessed channel id routes someone else's
        # reminder into someone else's room. Same rule the work-board tools
        # follow for team_id (see _work_board_mcp_tools._resolve_team_room).
        origin_source, origin_channel_id = await _caller_job_origin()
        return await get_agent_data_store().job_create(
            agent_id,
            {
                "user_id": user_id,
                "origin_source": origin_source,
                "origin_channel_id": origin_channel_id,
                "title": title,
                "description": description,
                "job_type": job_type,
                "trigger_config": dict(trigger_config),
                "payload": payload,
                "notification_method": notification_method,
                "task_key": task_key,
                "depends_on_job_ids": depends_on_job_ids,
                "related_entity_id": related_entity_id,
                "narrative_id": narrative_id,
                "confirm_new": confirm_new,
            },
        )

    # -----------------------------------------------------------------
    # Tool: job_retrieval_semantic
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_retrieval_semantic(
        agent_id: str,
        query: str,
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 10,
    ) -> dict:
        """
        Search jobs using natural language semantic similarity.

        Use this tool when you need to find jobs based on meaning rather
        than exact keyword matches. The search understands context and
        finds related jobs even if the exact words don't match.

        Args:
            agent_id: Your Agent ID (required)
            query: Natural language search query describing what you're looking for
            user_id: Optional filter by user ID
            status: Optional filter by status. Valid values:
                - "pending": Waiting for first trigger
                - "active": Active (scheduled/ongoing job running normally)
                - "running": Currently executing
                - "paused": Paused by manager
                - "completed": Finished (one_off completed, or ongoing reached end_condition)
                - "failed": Execution failed
                - "cancelled": Cancelled by manager
            limit: Maximum number of results (default: 10)

        Returns:
            dict with success status and list of matching jobs with similarity scores

        Examples:
            # Find news-related jobs
            job_retrieval_semantic(
                agent_id="agent_xxx",
                query="daily news updates and summaries"
            )

            # Find reminder tasks for a specific user
            job_retrieval_semantic(
                agent_id="agent_xxx",
                query="meeting reminders",
                user_id="user_123",
                status="active"
            )
        """
        # Routes through the seam. setup_mcp_llm_context is gone: search_keyword
        # is BM25 (vectors retired), so the call only added a db read + a raise
        # path. Status validation lives in the shared helper (parity).

        return await get_agent_data_store().job_retrieval_semantic(
            agent_id, query, user_id, status, limit
        )

    # -----------------------------------------------------------------
    # Tool: job_retrieval_by_id
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_retrieval_by_id(
        agent_id: str,
        job_id: str
    ) -> dict:
        """
        Retrieve a specific job by its ID.

        Use this tool when you know the exact job_id and need to get
        its full details.

        Args:
            agent_id: Your Agent ID (required for verification)
            job_id: The exact job ID to retrieve (e.g., "job_abc123")

        Returns:
            dict with success status and complete job details

        Example:
            job_retrieval_by_id(
                agent_id="agent_xxx",
                job_id="job_abc123"
            )
        """
        # Routes through the AgentDataStore seam (DirectStore local / HttpStore
        # cloud, via job_module's shared read helpers). Agent-scoping preserved.

        return await get_agent_data_store().job_retrieval_by_id(agent_id, job_id)

    # -----------------------------------------------------------------
    # Tool: job_retrieval_by_keywords
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_retrieval_by_keywords(
        agent_id: str,
        keywords: List[str],
        user_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """
        Search jobs by keyword matching.

        Use this tool for simple keyword-based search when you know
        specific terms that appear in job titles, descriptions, or payloads.

        Args:
            agent_id: Your Agent ID (required)
            keywords: List of keywords to search for (matches if ANY keyword found)
            user_id: Optional filter by user ID
            status: Optional filter by status (pending/active/running/paused/completed/failed/cancelled)
            limit: Maximum number of results (default: 20)

        Returns:
            dict with success status and list of matching jobs

        Example:
            job_retrieval_by_keywords(
                agent_id="agent_xxx",
                keywords=["news", "summary"],
                status="active"
            )
        """
        # Routes through the seam (see the other job reads).

        return await get_agent_data_store().job_retrieval_by_keywords(
            agent_id, keywords, user_id, status, limit
        )

    # -----------------------------------------------------------------
    # Tool: job_update (Feature 2.2.2)
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_update(
        agent_id: str,
        job_id: str,
        title: Optional[str] = None,
        description: Optional[str] = None,
        payload: Optional[str] = None,
        guidance_text: Optional[str] = None,
        trigger_config: OptionalTriggerConfigInput = None,
        job_type: Optional[str] = None,
        next_run_time: Optional[str] = None,
        status: Optional[str] = None,
        related_entity_id: Optional[str] = None
    ) -> dict:
        """
        Update an existing Job. Only passed fields change. New job → job_create;
        query → job_retrieval_by_id.

        Args:
            agent_id: Your Agent ID (authorization)
            job_id: Format "job_xxxxxxxx" (from job_retrieval_* tools)
            title: New title
            description: New description
            payload: New instruction — REPLACES the entire payload
            guidance_text: APPENDS a "## Manager Guidance" section to the
                payload (after new payload if both given)
            trigger_config: Same shapes/rules as job_create. EVERY shape
                REQUIRES "timezone" (IANA name); cron/run_at are wall-clock
                times IN that timezone (never converted to UTC); run_at naive
                ISO 8601 (no "Z"/offset). Shapes: one_off {"run_at","timezone"}; scheduled
                {"cron" OR "interval_seconds","timezone", optional "end_at"
                naive local = run until then, platform-completed}; ongoing
                {"interval_seconds","end_condition","timezone", optional
                "max_iterations" and "end_at" (same horizon as scheduled)}
            job_type: "one_off" | "scheduled" | "ongoing". WARNING: also
                update trigger_config to match the new type.
            next_run_time: Override next run, ISO8601 UTC ("...Z" or offset).
                For "execute now" / one-shot reschedule; lasting changes go
                via trigger_config (re-applies the job's frozen timezone).
            status: "active" (resume) | "paused" (skipped until resumed) |
                "cancelled" (TERMINAL, cannot undo)
            related_entity_id: New target user ID (who the job is for/about)

        Returns: dict(success, job_id, updated_fields, message)

        Example: job_update(agent_id="agent_1", job_id="job_abc1",
            trigger_config={"interval_seconds": 604800, "timezone": "Asia/Shanghai"})

        Common errors: job not found / other agent's; missing "timezone" or
        run_at with offset; invalid job_type/status; no fields passed.
        """
        # Routes through the AgentDataStore seam. The whole build-updates body
        # (effective_type ordering, trigger_config + compute_next_run, status
        # validation) is now the shared update_job_from_args (DirectStore local /
        # HttpStore cloud). A cross-agent job now reads as "not found" (no
        # existence oracle — the old tool leaked "does not belong to agent X").

        fields = {
            "title": title,
            "description": description,
            "payload": payload,
            "guidance_text": guidance_text,
            "trigger_config": dict(trigger_config) if trigger_config is not None else None,
            "job_type": job_type,
            "next_run_time": next_run_time,
            "status": status,
            "related_entity_id": related_entity_id,
        }
        return await get_agent_data_store().job_update(agent_id, job_id, fields)

    # -----------------------------------------------------------------
    # Tool: job_pause (Feature 2.2.2)
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_pause(
        agent_id: str,
        job_id: str
    ) -> dict:
        """
        Pause a Job (Feature 2.2.2 - Type C Operation)

        Set job status to PAUSED. The job will not be triggered by JobTrigger until resumed.

        Use case:
            Sales manager says: "Wait on contacting Alice until they finish their internal discussion"

        Args:
            agent_id: Agent ID (for authorization)
            job_id: Job ID to pause

        Returns:
            dict with success status and message

        Example:
            job_pause(
                agent_id="agent_123",
                job_id="job_xiaoming_followup"
            )
        """
        # Routes through the AgentDataStore seam (DirectStore local / HttpStore
        # cloud). pause_job_from_args owns ownership check + pause.
        return await get_agent_data_store().job_pause(agent_id, job_id)

    # -----------------------------------------------------------------
    # Tool: job_cancel (Feature 2.2.2)
    # -----------------------------------------------------------------
    @mcp.tool()
    async def job_cancel(
        agent_id: str,
        job_id: str
    ) -> dict:
        """
        Cancel a Job and clean up entity associations (Feature 2.2.2 - Type C Operation)

        Set job status to CANCELLED and remove from all related entities' related_job_ids.

        **Important**: This is a terminal operation. Cancelled jobs cannot be resumed.

        Use case:
            Sales manager says: "We're no longer following up with this customer, cancel all related tasks"

        Args:
            agent_id: Agent ID (for authorization)
            job_id: Job ID to cancel

        Returns:
            dict with success status and message

        Example:
            job_cancel(
                agent_id="agent_123",
                job_id="job_customer_followup"
            )
        """
        # Routes through the AgentDataStore seam (DirectStore local / HttpStore
        # cloud). cancel_job_from_args owns ownership check + cancel + the
        # best-effort entity unlink.
        return await get_agent_data_store().job_cancel(agent_id, job_id)

    return mcp
