"""
@file_name: _job_writes.py
@author:
@date: 2026-08-10
@description: Job WRITE helpers shared by the AgentDataStore seam (DirectStore),
the seam twin route, and the frontend job routes.

``update_job_from_args`` is the ~90-line job_update body (validate job_type /
trigger_config / next_run_time / status, build the ``updates`` dict, then
``JobInstanceService.update_job``) lifted into ONE place. It existed as three
drifting copies — the MCP tool, the ``/api/jobs/{job_id}`` frontend route, and
(after this migration) the agent-scoped seam route — and the ``effective_type``
/ ``compute_next_run`` ordering here is the load-bearing zombie-bug fix
(pre-open review #4): a one_off→scheduled switch with a new cron must compute
next_run against the NEW type, or the job silently never runs again. Keeping one
copy is the only safe way to not re-open that bug in one path.

Dialect-safe (JobRepository / JobInstanceService, no raw SQL). Returns the
COMPLETE result dict and never raises. Agent-scoped: a job owned by a different
agent reads as "not found" (no existence oracle — matches the route's posture,
which is SAFER than the old tool's "does not belong to agent X").
"""
from __future__ import annotations

from datetime import datetime, timezone as _dt_timezone
from typing import Optional
from zoneinfo import ZoneInfo

from loguru import logger

from narranexus.platform.repository import JobRepository
from narranexus.platform.schema.job_schema import JobStatus, JobType


async def update_job_from_args(
    db,
    agent_id: str,
    job_id: str,
    *,
    title: Optional[str] = None,
    description: Optional[str] = None,
    payload: Optional[str] = None,
    guidance_text: Optional[str] = None,
    trigger_config: Optional[dict] = None,
    job_type: Optional[str] = None,
    next_run_time: Optional[str] = None,
    status: Optional[str] = None,
    related_entity_id: Optional[str] = None,
) -> dict:
    """Update an existing job's fields (only the passed ones change)."""
    try:
        from narranexus_plugins.job_module.job_service import JobInstanceService

        job_repo = JobRepository(db)
        job = await job_repo.get_job(job_id)
        if not job or job.agent_id != agent_id:
            # A job owned by another agent reads as "not found" — no existence
            # oracle (the old MCP tool leaked "does not belong to agent X").
            return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}

        updates: dict = {}

        # Resolve the effective job_type up front: trigger_config's
        # compute_next_run runs BEFORE the job_type branch, so reading
        # updates.get("job_type") there would see the OLD type — a
        # one_off→scheduled switch with a new cron would compute next_run as
        # one_off (→ None), silently zombifying the job (pre-open review #4).
        if job_type is not None:
            try:
                effective_type = JobType(job_type.lower())
            except ValueError:
                return {"success": False, "job_id": job_id,
                        "message": f"Invalid job_type: {job_type}. Valid: one_off, scheduled, ongoing"}
            updates["job_type"] = effective_type
        else:
            effective_type = job.job_type

        if title is not None:
            updates["title"] = title
        if description is not None:
            updates["description"] = description
        if payload is not None:
            updates["payload"] = payload
        if guidance_text:
            base_payload = updates.get("payload", job.payload) or ""
            updates["payload"] = f"{base_payload}\n\n## Manager Guidance\n{guidance_text}"
        if trigger_config is not None:
            from narranexus.platform.schema.job_schema import TriggerConfig
            from narranexus.platform.utils.job_scheduling import compute_next_run
            from pydantic import ValidationError as _VE
            try:
                tc_model = TriggerConfig(**trigger_config)
            except _VE as ve:
                first = ve.errors()[0]
                loc = ".".join(str(p) for p in first.get("loc", ()))
                return {"success": False, "job_id": job_id,
                        "message": f"Invalid trigger_config ({loc}): {first['msg']}"}
            updates["trigger_config"] = tc_model
            nxt = compute_next_run(effective_type, tc_model)
            if nxt:
                updates["next_run_time"] = nxt.utc
                updates["next_run_at_local"] = nxt.local
                updates["next_run_tz"] = nxt.tz
            else:
                updates["next_run_time"] = None
                updates["next_run_at_local"] = None
                updates["next_run_tz"] = None
        if next_run_time is not None:
            # Atomic UTC + local-in-frozen-tz override so display and poller agree.
            try:
                next_utc = datetime.fromisoformat(next_run_time.replace("Z", "+00:00"))
                if next_utc.tzinfo is None:
                    next_utc = next_utc.replace(tzinfo=_dt_timezone.utc)
            except ValueError as e:
                return {"success": False, "job_id": job_id, "message": f"Invalid next_run_time format: {e}"}
            tz_name = (job.trigger_config.timezone if job.trigger_config else None) or "UTC"
            next_local = next_utc.astimezone(ZoneInfo(tz_name)).replace(tzinfo=None).isoformat()
            updates["next_run_time"] = next_utc
            updates["next_run_at_local"] = next_local
            updates["next_run_tz"] = tz_name
        if status is not None:
            try:
                updates["status"] = JobStatus(status.lower())
            except ValueError:
                return {"success": False, "job_id": job_id,
                        "message": f"Invalid status: {status}. Valid: active, paused, cancelled"}
        if related_entity_id is not None:
            updates["related_entity_id"] = related_entity_id

        if not updates:
            return {"success": False, "job_id": job_id, "message": "No fields to update"}

        return await JobInstanceService(db).update_job(job_id=job_id, updates=updates, agent_id=agent_id)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.update_job_from_args] failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}


async def _resolve_job_owner(db, agent_id: str, supplied: str) -> str:
    """Whose job this is — ``agents.created_by``, not whoever asked.

    A job's owner has a ground truth, and the supplied value often is not it.
    On a bus turn ``user_id`` is the SENDER (``message_bus_trigger`` passes
    ``sender_agent_id``), so a job asked for in a team room arrived here as
    ``usr_<uid>`` or a peer's ``agent_id`` — an owner that does not exist. The
    job was then filed under it: the owner's Jobs list stayed empty, and
    execution loaded a context belonging to nobody, while the agent reported
    success.

    Fixed HERE rather than in ``_mcp_identity.resolve_caller_user_id``, which
    overrides placeholders only and documents why a mismatching REAL value can
    be legitimate on the generic path. That judgement is about identity in
    general; "whose job is this" is a narrower question with an answer in the
    database, so it is answered where the row is written — one site, covering
    the local MCP process and the cloud seam route alike.

    Fails open. ``resolve_owner`` returns "" for "no such agent" and None for
    "the query failed"; neither is evidence the caller was wrong, and blanking
    the field would lose the job outright. Divergences are logged rather than
    silently corrected — that log line is the measurement the generic path's
    comment asks for before anyone widens this.

    ``related_entity_id`` is untouched: it answers "about whom", which is a
    different question and a supported shape.
    """
    from narranexus.platform.repository import AgentRepository

    try:
        owner = await AgentRepository(db).resolve_owner(agent_id)
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"[job.create] owner lookup failed for {agent_id!r}: {e}")
        return supplied
    if not owner:
        return supplied
    if owner != supplied:
        logger.info(
            f"[job.create] owner corrected agent={agent_id} "
            f"supplied={supplied!r} owner={owner!r}"
        )
    return owner


async def create_job_from_args(
    db,
    agent_id: str,
    *,
    user_id: str,
    title: str,
    description: str,
    job_type: str,
    trigger_config: dict,
    payload: str,
    notification_method: str = "direct",
    task_key: Optional[str] = None,
    depends_on_job_ids: Optional[list] = None,
    related_entity_id: Optional[str] = None,
    narrative_id: Optional[str] = None,
    origin_source: Optional[str] = None,
    origin_channel_id: Optional[str] = None,
    confirm_new: bool = False,
) -> dict:
    """Create a Job + its ModuleInstance (the job_create tool body, shared).

    ``setup_mcp_llm_context`` lives HERE, not in the tool: create_job_with_instance
    runs the similar-title embedding check, which needs the owner's LLM config on
    the ContextVar — and that config load reads the DB. So the setup MUST run in
    whichever process actually holds the DB: the mcp process for DirectStore
    (local), the backend for the seam route (cloud, where the mcp container has
    no creds). Keeping it in the tool would fail in cloud (no DB to load config
    from). Returns the COMPLETE result dict and never raises — the outer excepts
    preserve the W1 hardening (a raw exception here once produced "I can't do
    that" replies): LLMConfigNotConfigured is almost always a guessed agent_id,
    surfaced as an actionable retry; any other error becomes a structured dict.
    """
    from narranexus.platform.agent_framework.api_config import (
        setup_mcp_llm_context,
        LLMConfigNotConfigured,
    )
    from narranexus_plugins.job_module.job_service import JobInstanceService

    try:
        await setup_mcp_llm_context(agent_id)
        result = await JobInstanceService(db).create_job_with_instance(
            agent_id=agent_id,
            user_id=await _resolve_job_owner(db, agent_id, user_id),
            title=title,
            description=description,
            job_type=job_type,
            trigger_config=trigger_config,
            payload=payload,
            notification_method=notification_method,
            dependencies=depends_on_job_ids,
            related_entity_id=related_entity_id,
            narrative_id=narrative_id,
            origin_source=origin_source,
            origin_channel_id=origin_channel_id,
            confirm_new=confirm_new,
        )
        if result.get("success") and task_key:
            result["task_key"] = task_key
        return result
    except LLMConfigNotConfigured as e:
        # The one failure a model can fix by itself: it almost always means
        # agent_id was a guess. Raw exception text here read as "impossible"
        # and produced "I can't do that" replies (W1).
        logger.warning(f"[job.create] LLM context failed for agent_id={agent_id!r}: {e}")
        return {
            "success": False,
            "error": (
                f"Could not resolve the agent context for agent_id={agent_id!r}. "
                "Retry with the exact Agent ID stated in your instructions — "
                "never a placeholder like 'agent_current'."
            ),
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.create] failed: {e}")
        return {"success": False, "error": str(e)}


async def pause_job_from_args(db, agent_id: str, job_id: str) -> dict:
    """Pause a Job (set status PAUSED so JobTrigger skips it) — the job_pause
    tool body, shared. Agent-scoped: a job owned by another agent reads as "not
    found" (no existence oracle — matches update_job_from_args' posture, safer
    than the old tool's "does not belong to agent X"). Never raises."""
    try:
        job_repo = JobRepository(db)
        job = await job_repo.get_job(job_id)
        if not job or job.agent_id != agent_id:
            return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}

        updated_rows = await job_repo.pause_job(job_id)
        return {
            "success": updated_rows > 0,
            "job_id": job_id,
            "status": "paused",
            "message": "Job paused successfully" if updated_rows > 0 else "Failed to pause job",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.pause] failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}


async def cancel_job_from_args(db, agent_id: str, job_id: str) -> dict:
    """Cancel a Job (terminal) and unlink it from its related entity — the
    job_cancel tool body, shared. Same no-existence-oracle posture as pause. The
    entity cleanup is best-effort (logged, never fails the cancel). Never raises."""
    try:
        from narranexus.platform.repository import SocialNetworkRepository
        from narranexus_plugins.job_module.job_service import JobInstanceService

        job_repo = JobRepository(db)
        job = await job_repo.get_job(job_id)
        if not job or job.agent_id != agent_id:
            return {"success": False, "job_id": job_id, "message": f"Job {job_id} not found"}

        updated_rows = await job_repo.cancel_job(job_id)

        # Clean up entity associations (best-effort; a failure here must not
        # undo the cancel the caller already observed as done).
        if job.related_entity_id:
            social_instance_id = await JobInstanceService(db)._get_social_network_instance_id(agent_id)
            if social_instance_id:
                try:
                    await SocialNetworkRepository(db).remove_related_job_ids(
                        entity_id=job.related_entity_id,
                        instance_id=social_instance_id,
                        job_ids=[job_id],
                    )
                except Exception as e:  # noqa: BLE001
                    logger.exception(
                        f"[job.cancel] failed to remove job {job_id} from entity "
                        f"{job.related_entity_id}: {e}"
                    )

        return {
            "success": updated_rows > 0,
            "job_id": job_id,
            "status": "cancelled",
            "message": "Job cancelled successfully" if updated_rows > 0 else "Failed to cancel job",
        }
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[job.cancel] failed: {e}")
        return {"success": False, "job_id": job_id, "message": f"Error: {e}"}
