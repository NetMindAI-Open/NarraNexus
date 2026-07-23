"""
@file_name: auth.py
@author: NetMind.AI
@date: 2025-11-28
@description: REST API routes for authentication and user management

Provides endpoints for:
- POST /api/auth/login - Login with user_id
- POST /api/auth/create-user - Create a new user (requires admin secret key)
- GET /api/auth/agents - Get all agents for a user
- POST /api/auth/agents - Create a new agent
- PUT /api/auth/agents/{agent_id} - Update agent info
- DELETE /api/auth/agents/{agent_id} - Cascade delete agent and all related data
"""

import os
import json
from uuid import uuid4
from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client
from xyz_agent_context.utils import format_for_api
from xyz_agent_context.analytics import track, identify_user
from xyz_agent_context.analytics.events import (
    EVENT_SIGNED_UP, EVENT_SETUP_ENTERED, EVENT_SETUP_SKIPPED,
    EVENT_SETUP_COMPLETED, PROP_METHOD,
)

# Whitelist of frontend-reportable funnel events. The setup_* events are pure
# UI actions (page view, skip/done clicks) that have no backend signal, so the
# frontend reports them via POST /api/auth/funnel. Whitelisting stops the
# endpoint from being a generic event firehose.
_ALLOWED_FUNNEL_EVENTS = frozenset({
    EVENT_SETUP_ENTERED, EVENT_SETUP_SKIPPED, EVENT_SETUP_COMPLETED,
})
from xyz_agent_context.repository import (
    AgentRepository,
    UserRepository,
)
from xyz_agent_context.schema import (
    LoginRequest,
    LoginResponse,
    NetmindLoginRequest,
    NetmindLoginResponse,
    ActiveRunInfo,
    AgentInfo,
    AgentListResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    UpdateAgentRequest,
    UpdateAgentResponse,
    DeleteAgentResponse,
    CreateUserRequest,
    CreateUserResponse,
    UpdateTimezoneRequest,
    UpdateTimezoneResponse,
    OnboardingProgress,
    OnboardingResponse,
    UpdateOnboardingRequest,
)
from backend.auth import (
    create_token,
    _is_cloud_mode,
    resolve_current_user_id,
)
from xyz_agent_context.utils.deployment_mode import is_power_login_enabled
from xyz_agent_context.utils import is_valid_timezone
from xyz_agent_context.agent_runtime.background_run import run_is_live
from xyz_agent_context.settings import settings as app_settings

from pydantic import BaseModel
from xyz_agent_context.repository.user_settings_repository import UserSettingsRepository
from typing import Optional


router = APIRouter()


# Heartbeat-freshness liveness rule for events rows stuck at
# state='running' — shared with the WS reconnect path so "is this run
# actually alive?" has one answer. See run_is_live in background_run.py.
# Without this filter the sidebar avatar pulses "running" forever for an
# agent whose run task died without _finalize.
_run_is_live = run_is_live


def _schedule_login_rearm(user_id: str) -> None:
    """On login, kick a background edge-recovery: if the user is now provider-
    ready (e.g. they topped up / fixed config while away), revive their
    PAUSED_NO_QUOTA jobs. Non-blocking — login responds immediately."""
    try:
        from xyz_agent_context.module.job_module.job_recovery import (
            schedule_user_no_quota_rearm,
        )
        schedule_user_no_quota_rearm(user_id)
    except Exception:  # noqa: BLE001 — never let recovery wiring break login
        pass


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """
    Local-mode login with user_id only (OS user is the trust boundary).

    Cloud mode has no password login anymore — cloud identity lives in
    the NetMind account system; use POST /api/auth/netmind-login.
    """
    if _is_cloud_mode():
        raise HTTPException(
            status_code=404,
            detail="Password login is gone. Use /api/auth/netmind-login.",
        )

    logger.info(f"Login attempt for user: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        user = await user_repo.get_user(request.user_id)

        if not user:
            logger.warning(f"User {request.user_id} not found")
            return LoginResponse(
                success=False,
                error="User not found. Please contact administrator to create an account.",
            )

        await user_repo.update_last_login(request.user_id)
        logger.info(f"User {request.user_id} logged in (local)")
        _schedule_login_rearm(request.user_id)
        return LoginResponse(
            success=True,
            user_id=request.user_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error during login: {e}")
        return LoginResponse(success=False, error=str(e))


def _get_netmind_auth_client():
    """Factory for the NetMind token-verification client.

    Module-level indirection so tests can monkeypatch it; the client is
    cheap to construct (stateless besides config), so no caching.
    """
    from xyz_agent_context.services.netmind_auth_client import NetmindAuthClient

    return NetmindAuthClient()


@router.post("/netmind-login", response_model=NetmindLoginResponse)
async def netmind_login(request: NetmindLoginRequest, http_request: Request):
    """Log in with a NetMind account token ("passport for visa" exchange).

    Available wherever Power login is enabled — always on the cloud server,
    and on a local deployment that opted in (NARRANEXUS_ENABLE_POWER_LOGIN).
    The frontend obtains a NetMind loginToken (embedded
    login form / OAuth popup / ?token= URL pass-through from netmind.ai
    or Arena), we verify it once against NetMind's auth API, lazily
    upsert the local user (user_id = NetMind userSystemCode) and issue
    NarraNexus's own JWT. Subsequent requests are validated locally —
    NetMind availability never becomes a per-request dependency.

    Error mapping: invalid/expired NetMind token -> 401; NetMind
    unreachable or contract drift -> 502 (never disguised as a user
    credential failure).
    """
    from xyz_agent_context.services.netmind_auth_client import (
        NetmindAuthError,
        NetmindUpstreamError,
    )

    if not is_power_login_enabled():
        raise HTTPException(status_code=404, detail="Not available in local mode")

    try:
        netmind_user = await _get_netmind_auth_client().verify_token(
            request.netmind_token
        )
    except NetmindAuthError:
        raise HTTPException(status_code=401, detail="Invalid NetMind token")
    except NetmindUpstreamError as exc:
        logger.error(f"netmind-login: upstream failure: {exc}")
        raise HTTPException(
            status_code=502, detail="NetMind auth service unavailable, try again"
        )

    db_client = await get_db_client()
    user_repo = UserRepository(db_client)
    user, is_new = await user_repo.upsert_netmind_user(
        user_system_code=netmind_user.user_system_code,
        email=netmind_user.email,
        display_name=netmind_user.nickname,
    )

    # Seed the system-default free-tier quota on first login (registration
    # is gone — first login IS registration now). Failures must not fail
    # the login; staff can re-seed via /api/admin/quota/init.
    quota_row = None
    if is_new:
        quota_service = getattr(http_request.app.state, "quota_service", None)
        if quota_service is not None:
            try:
                quota_row = await quota_service.init_for_user(user.user_id)
            except Exception as e:
                logger.exception(
                    f"netmind-login: failed to init quota for {user.user_id}: {e}"
                )
        try:
            identify_user(user.user_id, {"signup_method": "netmind"})
            track(user.user_id, EVENT_SIGNED_UP, {PROP_METHOD: "netmind"})
        except Exception:  # noqa: BLE001 — analytics must never break login
            pass

    user_row = await db_client.get_one("users", {"user_id": user.user_id})
    role = (user_row.get("role") if user_row else None) or "user"

    token = create_token(user.user_id, role)
    logger.info(
        f"netmind-login ok: user={user.user_id} new={is_new} "
        f"source={request.source or '-'}"
    )
    _schedule_login_rearm(user.user_id)

    # Cloud login IS NetMind login — make the user's NetMind.AI Power credits
    # available automatically (mint key + register the netmind provider if
    # missing). Fire-and-forget + non-fatal: login must never block on or fail
    # from NetMind minting. Self-guards on the feature flag; register-only when
    # the user already has an active config (no slot hijack).
    from xyz_agent_context.services.netmind_provisioner import (
        schedule_ensure_netmind_provider,
    )
    schedule_ensure_netmind_provider(user.user_id, request.netmind_token)

    return NetmindLoginResponse(
        success=True,
        user_id=user.user_id,
        token=token,
        role=role,
        is_new_user=is_new,
        display_name=user.display_name,
        email=user.email,
        has_system_quota=quota_row is not None,
        initial_input_tokens=(quota_row.initial_input_tokens if quota_row else 0),
        initial_output_tokens=(quota_row.initial_output_tokens if quota_row else 0),
    )


@router.get("/agents", response_model=AgentListResponse)
async def get_agents(request: Request):
    """
    Get the list of agents visible to the user. Identity from auth_middleware.

    Visibility rules:
    - Agents created by the user (created_by = user_id)
    - Agents set as public (is_public = 1)

    History: ``user_id`` used to be a Query param the client supplied
    directly. That let any client list any other user's owned agents by
    swapping the value, and (in cloud mode) bypass the JWT identity.
    Identity is now strictly derived from auth_middleware.
    """
    user_id = await resolve_current_user_id(request)
    logger.debug(f"Getting agents list for user: {user_id}")

    try:
        db_client = await get_db_client()

        query = """
            SELECT
                agent_id,
                agent_name,
                agent_description,
                agent_type,
                agent_create_time,
                created_by,
                is_public,
                agent_metadata
            FROM agents
            WHERE created_by = %s OR is_public = 1
            ORDER BY agent_create_time DESC
        """
        rows = await db_client.execute(query, (user_id,))

        # Phase C: bulk-fetch any active runs in one SELECT so we don't
        # do N+1 queries when the user has many agents.
        agent_ids = [row['agent_id'] for row in rows]
        active_runs_by_agent: dict[str, dict] = {}
        if agent_ids:
            placeholders = ",".join(["%s"] * len(agent_ids))
            try:
                run_rows = await db_client.execute(
                    f"""
                    SELECT event_id, agent_id, state, started_at, last_event_at,
                           tool_call_count, current_stage
                    FROM events
                    WHERE state = 'running' AND user_id = %s
                      AND agent_id IN ({placeholders})
                    """,
                    (user_id, *agent_ids),
                )
                # Keep the latest started_at per agent if there are
                # somehow multiple (should not happen but defensive).
                for r in run_rows or []:
                    aid = r.get('agent_id')
                    if not aid:
                        continue
                    # Skip rows whose heartbeat has died — a dead run must
                    # not keep the sidebar avatar pulsing "running".
                    if not _run_is_live(r):
                        continue
                    existing = active_runs_by_agent.get(aid)
                    if existing is None or (r.get('started_at') or "") > (existing.get('started_at') or ""):
                        active_runs_by_agent[aid] = r
            except Exception as e:  # noqa: BLE001
                # Don't fail the whole listing because the active-run
                # enrichment broke — log and continue with no active_run
                # info attached.
                logger.warning(f"[/api/auth/agents] active_run enrichment failed: {e}")

        # NM sidebar preview — one window-function SELECT pulls the most
        # recent persisted assistant reply per agent in this list. Uses
        # ROW_NUMBER() so we get exactly one row per agent without an
        # N+1 sweep. Both SQLite (>=3.25) and MySQL 8+ support window
        # functions, which are the two backends auto_migrate ships.
        # final_output IS NOT NULL filters out events that crashed before
        # producing a reply; an empty string is treated the same since
        # the user wouldn't want "" rendered as preview.
        last_assistant_by_agent: dict[str, dict] = {}
        if agent_ids:
            placeholders = ",".join(["%s"] * len(agent_ids))
            try:
                preview_rows = await db_client.execute(
                    f"""
                    SELECT agent_id, final_output, created_at FROM (
                        SELECT agent_id, final_output, created_at,
                               ROW_NUMBER() OVER (
                                   PARTITION BY agent_id ORDER BY created_at DESC
                               ) AS rn
                        FROM events
                        WHERE agent_id IN ({placeholders})
                          AND final_output IS NOT NULL
                          AND final_output != ''
                          -- 1:1 sidebar preview only — exclude team group-chat
                          -- (message bus) replies. New runs are tagged at
                          -- creation (step_0_initialize); see the note below on
                          -- why historical rows can't be filtered reliably.
                          AND (`trigger` IS NULL OR `trigger` != 'message_bus')
                    ) ranked
                    WHERE rn = 1
                    """,
                    tuple(agent_ids),
                )
                for r in preview_rows or []:
                    aid = r.get('agent_id')
                    if aid:
                        last_assistant_by_agent[aid] = r
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    f"[/api/auth/agents] last_assistant enrichment failed: {e}"
                )

        agents = []
        for row in rows:
            description = row.get('agent_description')
            # Check if Bootstrap.md exists for this agent (first-run setup pending)
            bootstrap_active = False
            created_by = row.get('created_by')
            if created_by:
                from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace
                bootstrap_path = os.path.join(
                    str(resolve_existing_workspace(
                        row['agent_id'], created_by, app_settings.base_working_path
                    )),
                    "Bootstrap.md"
                )
                bootstrap_active = os.path.isfile(bootstrap_path)

            # Per-agent greeting override (only meaningful while bootstrapping).
            # The frontend's instant greeting must match the DB-persisted one,
            # so both read agent_metadata.bootstrap_greeting.
            bootstrap_greeting = None
            if bootstrap_active:
                raw_meta = row.get('agent_metadata')
                if raw_meta:
                    try:
                        meta = raw_meta if isinstance(raw_meta, dict) else json.loads(raw_meta)
                        bootstrap_greeting = (meta or {}).get('bootstrap_greeting')
                    except (ValueError, TypeError):
                        bootstrap_greeting = None

            active_run = None
            ar_row = active_runs_by_agent.get(row['agent_id'])
            if ar_row:
                active_run = ActiveRunInfo(
                    run_id=ar_row.get('event_id') or "",
                    state=ar_row.get('state') or "running",
                    started_at=format_for_api(ar_row.get('started_at')),
                    last_event_at=format_for_api(ar_row.get('last_event_at')),
                    tool_call_count=int(ar_row.get('tool_call_count') or 0),
                    current_stage=ar_row.get('current_stage') or None,
                )

            # NM sidebar preview — flatten whitespace and truncate so the
            # wire payload stays bounded. Frontend may slice further for
            # its row width, but 200 chars covers both alphabetic and
            # CJK width comfortably without bloating the response.
            last_assistant_preview = None
            last_assistant_at = None
            la_row = last_assistant_by_agent.get(row['agent_id'])
            if la_row:
                raw = la_row.get('final_output') or ""
                if raw:
                    flat = " ".join(raw.split())
                    last_assistant_preview = (
                        flat[:200] if len(flat) <= 200 else flat[:200].rstrip() + "…"
                    )
                last_assistant_at = format_for_api(la_row.get('created_at'))

            agent_info = AgentInfo(
                agent_id=row['agent_id'],
                name=row.get('agent_name') or row['agent_id'],
                description=description[:200] + '...' if description and len(description) > 200 else description,
                status='active',
                created_at=format_for_api(row.get('agent_create_time')),
                is_public=bool(row.get('is_public', 0)),
                created_by=created_by,
                bootstrap_active=bootstrap_active,
                bootstrap_greeting=bootstrap_greeting,
                active_run=active_run,
                last_assistant_preview=last_assistant_preview,
                last_assistant_at=last_assistant_at,
            )
            agents.append(agent_info)

        # First-paint ordering — most-recently-active conversation on top, so
        # the sidebar is already sorted before the frontend hydrates its local
        # sessions. Activity = latest of (last assistant reply, agent creation).
        # format_for_api emits fixed-width ISO-UTC ("...Z"), which sorts
        # lexically, so plain string max/compare is correct here. The frontend
        # re-sorts with the same rule PLUS fresh local session activity (see
        # sortAgentsByActivity); this is only the pre-hydration baseline.
        # Two stable passes: agent_id asc as the tie-breaker, then activity
        # desc — matching the frontend's deterministic ordering.
        agents.sort(key=lambda a: a.agent_id)
        agents.sort(
            key=lambda a: max(a.last_assistant_at or "", a.created_at or ""),
            reverse=True,
        )

        logger.debug(f"Found {len(agents)} agents for user {user_id}")

        return AgentListResponse(
            success=True,
            agents=agents,
            count=len(agents),
        )

    except Exception as e:
        logger.exception(f"Error getting agents: {e}")
        return AgentListResponse(
            success=False,
            error=str(e)
        )


@router.post("/agents", response_model=CreateAgentResponse)
async def create_agent(http_request: Request, request: CreateAgentRequest):
    """
    Create a new agent with default values.
    Generates a unique agent_id automatically. Identity (created_by) comes
    from auth_middleware — clients can no longer create agents under
    someone else's account by writing a different id into the body.
    """
    created_by = await resolve_current_user_id(http_request)
    logger.info(f"Creating new agent for user: {created_by}")

    try:
        db_client = await get_db_client()

        # Validate that the user exists
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(created_by)
        if not user:
            logger.warning(f"Cannot create agent: user {created_by} not found")
            return CreateAgentResponse(
                success=False,
                error="User not found. Please create an account first."
            )

        # Generate unique agent_id
        agent_id = f"agent_{uuid4().hex[:12]}"

        # Set default name if not provided
        agent_name = request.agent_name or "New Agent"
        agent_description = request.agent_description or "A new agent ready for configuration"

        # Add agent to database
        repo = AgentRepository(db_client)
        record_id = await repo.add_agent(
            agent_id=agent_id,
            agent_name=agent_name,
            created_by=created_by,
            agent_description=agent_description,
            agent_type="chat"
        )

        logger.info(f"Agent created: {agent_id}, record_id: {record_id}")

        # Create the default agent-level instances (Awareness, SocialNetwork,
        # BasicInfo, MessageBus, Lark). Without this the HTTP-created agent has
        # no AwarenessModule instance and downstream provisioning / awareness
        # writes have nothing to attach to. Idempotent (factory checks first);
        # best-effort so a transient failure never blocks agent creation.
        try:
            from xyz_agent_context.module._module_impl.instance_factory import InstanceFactory
            await InstanceFactory(db_client).create_agent_level_instances(agent_id)
        except Exception as inst_err:
            logger.warning(f"Failed to create default instances for {agent_id}: {inst_err}")

        # First-run flow via a bootstrap PROFILE (default = today's behavior).
        # The profile renders Bootstrap.md + the greeting + the deletion rule and
        # apply_bootstrap stores them (workspace + agent_metadata). Pass
        # `bootstrap` in the request to pick a profile; unknown/None → "default".
        from xyz_agent_context.settings import settings
        from xyz_agent_context.utils.workspace_paths import agent_workspace_path
        workspace_path = str(
            agent_workspace_path(agent_id, created_by, base=settings.base_working_path)
        )
        bootstrap_active = False
        try:
            from xyz_agent_context.bootstrap.profiles import (
                apply_bootstrap, get_profile, BootstrapContext,
            )
            profile = get_profile(getattr(request, "bootstrap", None) or "default")
            await apply_bootstrap(
                db_client,
                agent_id=agent_id,
                user_id=created_by,
                profile=profile,
                ctx=BootstrapContext(
                    agent_id=agent_id, user_id=created_by, agent_name=agent_name,
                ),
            )
            bootstrap_active = os.path.isfile(os.path.join(workspace_path, "Bootstrap.md"))
            logger.info(f"Bootstrap profile '{profile.name}' applied to {agent_id}")
        except Exception as bootstrap_err:
            # Non-fatal: agent is already created, bootstrap is best-effort
            logger.warning(f"Failed to apply bootstrap profile: {bootstrap_err}")

        # Team assignment (#43): when the sidebar "Add agent" was clicked under
        # a specific team, attach the new agent to that team so it lands in the
        # right group instead of Ungrouped. Best-effort + ownership-checked: a
        # missing or foreign team_id never blocks creation — it just leaves the
        # agent ungrouped.
        if request.team_id:
            try:
                from xyz_agent_context.repository import (
                    TeamRepository,
                    TeamMemberRepository,
                )
                team = await TeamRepository(db_client).get_team(request.team_id)
                if team and team.owner_user_id == created_by:
                    await TeamMemberRepository(db_client).add_member(
                        request.team_id, agent_id
                    )
                    logger.info(f"Agent {agent_id} added to team {request.team_id}")
                else:
                    logger.warning(
                        f"Skip team assignment for {agent_id}: team "
                        f"{request.team_id} missing or not owned by {created_by}"
                    )
            except Exception as team_err:
                logger.warning(f"Failed to assign {agent_id} to team: {team_err}")

        # Default skills — fire-and-forget install of every marketplace skill
        # flagged is_default into the new agent's workspace. Never blocks or
        # fails creation: an unreachable registry (desktop offline, cloud
        # marketplace not yet live) degrades to a no-op inside the service.
        try:
            import asyncio as _asyncio

            from xyz_agent_context.skill_marketplace_service import SkillMarketplaceService

            async def _install_default_skills(aid: str, uid: str) -> None:
                try:
                    summary = await SkillMarketplaceService().install_defaults(aid, uid)
                    if summary.get("failed"):
                        logger.warning(
                            f"Default skills for {aid}: failed={summary['failed']}"
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Default skills install for {aid} skipped: {exc}")

            _task = _asyncio.create_task(_install_default_skills(agent_id, created_by))
            # Fire-and-forget needs a done callback (incident lesson #2);
            # the inner try/except already swallows, this catches cancellation-
            # adjacent surprises.
            _task.add_done_callback(
                lambda t: (
                    logger.warning(f"default-skills task died: {t.exception()}")
                    if not t.cancelled() and t.exception() is not None
                    else None
                )
            )
        except Exception as defaults_err:  # noqa: BLE001
            logger.warning(f"Failed to schedule default skills: {defaults_err}")

        # Return the created agent info
        # Re-fetch from DB to get server-generated fields (created_at)
        agent_row = await db_client.get_one("agents", {"agent_id": agent_id})
        agent_info = AgentInfo(
            agent_id=agent_id,
            name=agent_name,
            description=agent_description,
            status='active',
            created_at=format_for_api(agent_row.get("agent_create_time")) if agent_row else None,
            created_by=created_by,
            bootstrap_active=bootstrap_active,
        )

        return CreateAgentResponse(
            success=True,
            agent=agent_info,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error creating agent: {e}")
        return CreateAgentResponse(
            success=False,
            error=str(e)
        )


@router.put("/agents/{agent_id}", response_model=UpdateAgentResponse)
async def update_agent(
    agent_id: str,
    body: UpdateAgentRequest,
    http_request: Request,
):
    """
    Update agent information (name, description)

    Ownership: in cloud mode (JWT-bound user_id on ``request.state``),
    only the creator may update. Local mode (no auth middleware) lets
    everything through — same model as the Slack/Telegram bind routes.
    """
    logger.info(f"Updating agent: {agent_id}")

    try:
        db_client = await get_db_client()
        repo = AgentRepository(db_client)

        # Check if the agent exists
        agent = await repo.get_agent(agent_id)
        if not agent:
            return UpdateAgentResponse(
                success=False,
                error=f"Agent {agent_id} not found"
            )

        # Ownership check: parallel to DELETE /agents/{agent_id} below
        # and to ``_verify_agent_ownership`` in the IM routes. Mutating
        # someone else's agent name from inside the same workspace was
        # the actual gap — DELETE was guarded, PUT was not.
        user_id = getattr(http_request.state, "user_id", None) or None
        if user_id and agent.created_by != user_id:
            return UpdateAgentResponse(
                success=False,
                error="Permission denied: only the creator can update this agent.",
            )

        request = body  # preserve old local var name in body below

        # Build update data
        update_data = {}
        if request.agent_name is not None:
            update_data["agent_name"] = request.agent_name
        if request.agent_description is not None:
            update_data["agent_description"] = request.agent_description
        if request.is_public is not None:
            update_data["is_public"] = int(request.is_public)

        if not update_data:
            return UpdateAgentResponse(
                success=False,
                error="No fields to update"
            )

        # Execute update
        affected_rows = await repo.update_agent(agent_id, update_data)

        if affected_rows > 0:
            # Get the updated agent info
            updated_agent = await repo.get_agent(agent_id)
            # Check bootstrap_active (Bootstrap.md exists in workspace)
            from xyz_agent_context.settings import settings
            from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace
            workspace_path = str(resolve_existing_workspace(
                agent_id, updated_agent.created_by, settings.base_working_path
            ))
            bootstrap_active = os.path.isfile(os.path.join(workspace_path, "Bootstrap.md"))

            agent_info = AgentInfo(
                agent_id=updated_agent.agent_id,
                name=updated_agent.agent_name,
                description=updated_agent.agent_description,
                status='active',
                created_at=format_for_api(updated_agent.agent_create_time),
                is_public=updated_agent.is_public,
                created_by=updated_agent.created_by,
                bootstrap_active=bootstrap_active,
            )
            logger.info(f"Agent {agent_id} updated successfully")

            return UpdateAgentResponse(
                success=True,
                agent=agent_info,
            )
        else:
            return UpdateAgentResponse(
                success=False,
                error="No changes made"
            )

    except Exception as e:
        logger.exception(f"Error updating agent: {e}")
        return UpdateAgentResponse(
            success=False,
            error=str(e)
        )


@router.delete("/agents/{agent_id}", response_model=DeleteAgentResponse)
async def delete_agent(
    agent_id: str,
    request: Request,
):
    """
    Cascade delete an Agent and all its associated data

    Permission: Only the Agent creator (created_by == user_id) can delete.
    Identity comes from auth_middleware — the old "operator's user_id"
    query param was directly compared against ``agent.created_by``,
    which let a client pass any value (including the real creator's
    user_id) to pass the permission check and nuke someone else's agent.
    Now ``user_id`` is the JWT/X-User-Id identity and unforgeable.

    Deletion order is from leaf to root to ensure foreign key safety:
    1. Instance Memory dynamic tables
    2. Narrative Memory dynamic tables
    3. Jobs
    4. Instance-Narrative Links
    5. Instance subsidiary data (social_entities, awareness, module_report_memory)
    6. Module Instances
    7. Events
    8. Narratives
    9. MCP URLs
    10. Agent Messages
    11. The Agent itself
    """
    user_id = await resolve_current_user_id(request)
    logger.info(f"Delete agent request: agent_id={agent_id}, user_id={user_id}")

    try:
        db_client = await get_db_client()
        repo = AgentRepository(db_client)

        # 1. Permission check: only the creator can delete
        agent = await repo.get_agent(agent_id)
        if not agent:
            return DeleteAgentResponse(
                success=False,
                agent_id=agent_id,
                error=f"Agent {agent_id} not found",
            )

        if agent.created_by != user_id:
            return DeleteAgentResponse(
                success=False,
                agent_id=agent_id,
                error="Permission denied: only the creator can delete this agent",
            )

        stats: dict[str, int] = {}

        # 2. Collect all associated instance_ids
        inst_rows = await db_client.execute(
            "SELECT instance_id FROM module_instances WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        instance_ids = [r["instance_id"] for r in inst_rows] if inst_rows else []

        # 3. Collect all associated narrative_ids
        nar_rows = await db_client.execute(
            "SELECT narrative_id FROM narratives WHERE agent_id = %s",
            (agent_id,),
            fetch=True,
        )
        narrative_ids = [r["narrative_id"] for r in nar_rows] if nar_rows else []

        # 4. Discover dynamic Memory tables (compatible with both MySQL and SQLite)
        is_sqlite = hasattr(db_client, '_backend') and db_client._backend and db_client._backend.dialect == "sqlite"
        if is_sqlite:
            mem_rows = await db_client.execute(
                """
                SELECT name AS tbl FROM sqlite_master
                WHERE type='table'
                  AND (name LIKE 'json_format_event_memory_%'
                       OR name LIKE 'instance_json_format_memory_%')
                """,
                params=(),
                fetch=True,
            )
        else:
            mem_rows = await db_client.execute(
                """
                SELECT TABLE_NAME AS tbl FROM information_schema.tables
                WHERE table_schema = DATABASE()
                  AND (TABLE_NAME LIKE 'json_format_event_memory_%%'
                       OR TABLE_NAME LIKE 'instance_json_format_memory_%%')
                """,
                params=(),
                fetch=True,
            )
        memory_tables = [r["tbl"] for r in mem_rows] if mem_rows else []

        # ========== Delete from leaf to root ==========

        # 4a. Instance Memory dynamic tables (by instance_id)
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            for tbl in memory_tables:
                if tbl.startswith("instance_json_format_memory_"):
                    result = await db_client.execute(
                        f"DELETE FROM `{tbl}` WHERE instance_id IN ({ph})",
                        tuple(instance_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[tbl] = cnt

        # 4b. Narrative Memory dynamic tables (by narrative_id)
        if narrative_ids:
            ph_n = ", ".join(["%s"] * len(narrative_ids))
            for tbl in memory_tables:
                if tbl.startswith("json_format_event_memory_"):
                    result = await db_client.execute(
                        f"DELETE FROM `{tbl}` WHERE narrative_id IN ({ph_n})",
                        tuple(narrative_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[tbl] = cnt

        # 5. Jobs (by agent_id)
        result = await db_client.execute(
            "DELETE FROM instance_jobs WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["instance_jobs"] = cnt

        # 6. Instance-Narrative Links (by instance_id)
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            result = await db_client.execute(
                f"DELETE FROM instance_narrative_links WHERE instance_id IN ({ph})",
                tuple(instance_ids),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["instance_narrative_links"] = cnt

        # 7. Instance subsidiary data (by instance_id)
        #     (instance_social_entities retired — entities now live in
        #      memory_entity, cleaned by agent_id in the memory sweep below.)
        instance_sub_tables = [
            "instance_awareness",
            "instance_module_report_memory",
            "instance_json_format_memory",
            # Was missing — separate single-row-per-instance memory table
            # used by the chat-format memory writer.
            "instance_json_format_memory_chat",
            # `module_report_memory` is also keyed by instance_id (per-instance
            # report blob, distinct from instance_module_report_memory).
            "module_report_memory",
        ]
        if instance_ids:
            ph = ", ".join(["%s"] * len(instance_ids))
            for sub_tbl in instance_sub_tables:
                try:
                    result = await db_client.execute(
                        f"DELETE FROM `{sub_tbl}` WHERE instance_id IN ({ph})",
                        tuple(instance_ids),
                        fetch=False,
                    )
                    cnt = result if isinstance(result, int) else 0
                    if cnt > 0:
                        stats[sub_tbl] = cnt
                except Exception:
                    # Table may not exist, skip
                    pass

        # 7b. Unified memory tables (by agent_id) — observation/entity/chat/...
        # are all agent-scoped; without this an account deletion would leave
        # orphaned memory rows (entities, learned facts, etc.).
        from xyz_agent_context.utils.schema_registry import MEMORY_KINDS
        for _kind in MEMORY_KINDS:
            _tbl = f"memory_{_kind}"
            try:
                result = await db_client.execute(
                    f"DELETE FROM `{_tbl}` WHERE agent_id = %s",
                    (agent_id,),
                    fetch=False,
                )
                cnt = result if isinstance(result, int) else 0
                if cnt > 0:
                    stats[_tbl] = cnt
            except Exception:
                pass  # Table may not exist on older DBs — skip.

        # 8. Module Instances (by agent_id)
        result = await db_client.execute(
            "DELETE FROM module_instances WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["module_instances"] = cnt

        # 9. Events (by agent_id)
        result = await db_client.execute(
            "DELETE FROM events WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["events"] = cnt

        # 10. Narratives (by agent_id)
        result = await db_client.execute(
            "DELETE FROM narratives WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["narratives"] = cnt

        # 11. MCP URLs (by agent_id)
        result = await db_client.execute(
            "DELETE FROM mcp_urls WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["mcp_urls"] = cnt

        # 12. Agent Messages (by agent_id)
        try:
            result = await db_client.execute(
                "DELETE FROM agent_messages WHERE agent_id = %s",
                (agent_id,),
                fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["agent_messages"] = cnt
        except Exception:
            pass

        # 13. Workspace directory
        try:
            import os
            import shutil
            from xyz_agent_context.settings import settings
            from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace
            workspace_path = str(
                resolve_existing_workspace(
                    agent_id, agent.created_by, settings.base_working_path
                )
            )
            if os.path.isdir(workspace_path):
                shutil.rmtree(workspace_path)
                stats["workspace_dir"] = 1
                logger.info(f"Deleted workspace: {workspace_path}")
        except Exception as e:
            logger.warning(f"Workspace cleanup failed (non-critical): {e}")

        # 14. Channel cleanups — registry-driven walk over every
        # ChannelModuleBase subclass in MODULE_MAP. Each subclass owns
        # its own cleanup_for_agent (default: credential row + inbox
        # channels by channel_id prefix; Lark overrides to also drop
        # CLI profile + workspace dir). Adding a new IM channel requires
        # zero edits here.
        try:
            from xyz_agent_context.channel.channel_module_base import (
                ChannelModuleBase,
            )
            from xyz_agent_context.module import MODULE_MAP

            for module_name, cls in MODULE_MAP.items():
                if not (isinstance(cls, type) and issubclass(cls, ChannelModuleBase)):
                    continue
                try:
                    mod = cls(
                        agent_id=agent_id,
                        user_id=user_id,
                        database_client=db_client,
                    )
                    result_stats = await mod.cleanup_for_agent(agent_id, db_client)
                    stats.update(result_stats)
                except Exception as e:  # noqa: BLE001
                    logger.warning(
                        f"Channel cleanup for {module_name} failed (non-critical): {e}"
                    )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Channel cleanup walk failed (non-critical): {e}")

        # 14b. team_members (subproject 1) — drop this agent from every team it's a member of.
        # Without this, the team panel keeps showing the deleted agent_id as a ghost member.
        try:
            result = await db_client.execute(
                "DELETE FROM team_members WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            cnt = result if isinstance(result, int) else 0
            if cnt > 0:
                stats["team_members"] = cnt
        except Exception as e:
            logger.warning(f"team_members cleanup failed: {e}")

        # 14c. Full message-bus cascade (the existing block only cleaned `lark_*` channels).
        # Strategy:
        #   - Pull every channel where this agent is a member (any prefix).
        #   - Remove this agent's membership.
        #   - For channels created_by this agent, OR channels whose membership is
        #     now empty, blow away the channel + its messages.
        #   - Drop diagnostic / registry rows for this agent.
        try:
            members_for_self = await db_client.get("bus_channel_members", {"agent_id": agent_id})
            channels_touched = {m.get("channel_id") for m in members_for_self if m.get("channel_id")}

            # Remove this agent's membership rows
            mres = await db_client.execute(
                "DELETE FROM bus_channel_members WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            mcnt = mres if isinstance(mres, int) else 0
            if mcnt > 0:
                stats["bus_channel_members"] = mcnt

            # For each touched channel: if no members remain OR the agent created it, delete the channel + messages
            killed_channels: list[str] = []
            for cid in channels_touched:
                ch_row = await db_client.get_one("bus_channels", {"channel_id": cid})
                remaining = await db_client.get("bus_channel_members", {"channel_id": cid})
                creator_match = ch_row and ch_row.get("created_by") == agent_id
                if not remaining or creator_match:
                    killed_channels.append(cid)

            if killed_channels:
                ph = ", ".join(["%s"] * len(killed_channels))
                msgs_res = await db_client.execute(
                    f"DELETE FROM bus_messages WHERE channel_id IN ({ph})",
                    tuple(killed_channels), fetch=False,
                )
                msgs_cnt = msgs_res if isinstance(msgs_res, int) else 0
                if msgs_cnt > 0:
                    stats["bus_messages"] = stats.get("bus_messages", 0) + msgs_cnt

                # Also drop their remaining members (if any) — channel is gone
                others_res = await db_client.execute(
                    f"DELETE FROM bus_channel_members WHERE channel_id IN ({ph})",
                    tuple(killed_channels), fetch=False,
                )
                others_cnt = others_res if isinstance(others_res, int) else 0
                if others_cnt > 0:
                    stats["bus_channel_members"] = stats.get("bus_channel_members", 0) + others_cnt

                ch_res = await db_client.execute(
                    f"DELETE FROM bus_channels WHERE channel_id IN ({ph})",
                    tuple(killed_channels), fetch=False,
                )
                ch_cnt = ch_res if isinstance(ch_res, int) else 0
                if ch_cnt > 0:
                    stats["bus_channels"] = ch_cnt

            # Registry: agent's "I'm alive" advertisement on the bus
            reg_res = await db_client.execute(
                "DELETE FROM bus_agent_registry WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            reg_cnt = reg_res if isinstance(reg_res, int) else 0
            if reg_cnt > 0:
                stats["bus_agent_registry"] = reg_cnt

            # Diagnostic: per-(message, agent) failure log
            mf_res = await db_client.execute(
                "DELETE FROM bus_message_failures WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            mf_cnt = mf_res if isinstance(mf_res, int) else 0
            if mf_cnt > 0:
                stats["bus_message_failures"] = mf_cnt
        except Exception as e:
            logger.warning(f"bus cascade cleanup failed (non-critical): {e}")

        # 14e. Orphan inbox entries pointing at deleted events
        try:
            if is_sqlite:
                ib_sql = (
                    "DELETE FROM inbox_table WHERE event_id IS NOT NULL "
                    "AND event_id NOT IN (SELECT event_id FROM events)"
                )
            else:
                ib_sql = (
                    "DELETE FROM inbox_table WHERE event_id IS NOT NULL "
                    "AND event_id NOT IN (SELECT event_id FROM events)"
                )
            ib_res = await db_client.execute(ib_sql, (), fetch=False)
            ib_cnt = ib_res if isinstance(ib_res, int) else 0
            if ib_cnt > 0:
                stats["inbox_table_orphans"] = ib_cnt
        except Exception as e:
            logger.warning(f"inbox orphan sweep failed (non-critical): {e}")

        # 14f. Per-agent LLM slot overrides (agent_slots). Keyed by agent_id;
        # without this, a deleted agent's override rows linger as orphans.
        try:
            as_res = await db_client.execute(
                "DELETE FROM agent_slots WHERE agent_id = %s",
                (agent_id,), fetch=False,
            )
            as_cnt = as_res if isinstance(as_res, int) else 0
            if as_cnt > 0:
                stats["agent_slots"] = as_cnt
        except Exception as e:
            logger.warning(f"agent_slots cascade cleanup failed (non-critical): {e}")

        # 15. The Agent itself
        result = await db_client.execute(
            "DELETE FROM agents WHERE agent_id = %s",
            (agent_id,),
            fetch=False,
        )
        cnt = result if isinstance(result, int) else 0
        if cnt > 0:
            stats["agents"] = cnt

        total = sum(stats.values())
        logger.info(f"Agent {agent_id} deleted, total {total} rows removed: {stats}")

        return DeleteAgentResponse(
            success=True,
            agent_id=agent_id,
            deleted_counts=stats,
        )

    except Exception as e:
        logger.exception(f"Error deleting agent {agent_id}: {e}")
        return DeleteAgentResponse(
            success=False,
            agent_id=agent_id,
            error=str(e),
        )


@router.post("/create-user", response_model=CreateUserResponse)
async def create_user(request: CreateUserRequest):
    """
    Create a new local user (local mode only).

    In cloud mode this endpoint is gone: it sat in AUTH_EXEMPT_PATHS with
    no credential check, i.e. an open account-creation hole. Cloud users
    are provisioned via netmind-login.
    """
    if _is_cloud_mode():
        raise HTTPException(status_code=404, detail="Not available in cloud mode")

    logger.info(f"Create user request for: {request.user_id}")

    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user already exists
        existing_user = await user_repo.get_user(request.user_id)
        if existing_user:
            logger.warning(f"User {request.user_id} already exists")
            return CreateUserResponse(
                success=False,
                error="User already exists"
            )

        # Create new user
        await user_repo.add_user(
            user_id=request.user_id,
            user_type="individual",
            display_name=request.display_name or request.user_id,
        )

        logger.info(f"User {request.user_id} created successfully")
        # Only non-identifying traits — the distinct_id is hashed and we
        # deliberately do NOT ship display_name, so no real names reach
        # PostHog.
        await identify_user(
            user_id=request.user_id,
            traits={"role": "individual"},
        )
        await track(
            user_id=request.user_id,
            event=EVENT_SIGNED_UP,
            properties={PROP_METHOD: "create_user"},
        )
        return CreateUserResponse(
            success=True,
            user_id=request.user_id,
        )

    except Exception as e:
        logger.exception(f"Error creating user: {e}")
        return CreateUserResponse(
            success=False,
            error=str(e)
        )


@router.post("/timezone", response_model=UpdateTimezoneResponse)
async def update_timezone(http_request: Request, request: UpdateTimezoneRequest):
    """
    Update the authenticated user's timezone.

    Automatically called when the browser page loads to sync the user's local
    timezone setting. IANA format, e.g. 'Asia/Shanghai'. Identity comes from
    auth_middleware — the body no longer carries (or trusts) a user_id.
    """
    user_id = await resolve_current_user_id(http_request)
    logger.info(f"Timezone update request: user={user_id}, timezone={request.timezone}")

    try:
        # Validate timezone format
        if not is_valid_timezone(request.timezone):
            logger.warning(f"Invalid timezone format: {request.timezone}")
            return UpdateTimezoneResponse(
                success=False,
                error=f"Invalid timezone format: {request.timezone}. Use IANA format like 'Asia/Shanghai'"
            )

        db_client = await get_db_client()
        user_repo = UserRepository(db_client)

        # Check if user exists
        user = await user_repo.get_user(user_id)
        if not user:
            logger.warning(f"User {user_id} not found")
            return UpdateTimezoneResponse(
                success=False,
                error="User not found"
            )

        # Update timezone
        await user_repo.update_timezone(user_id, request.timezone)

        logger.info(f"User {user_id} timezone updated to {request.timezone}")
        return UpdateTimezoneResponse(
            success=True,
            user_id=user_id,
            timezone=request.timezone,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error updating timezone: {e}")
        return UpdateTimezoneResponse(
            success=False,
            error=str(e)
        )


# =============================================================================
# Onboarding checklist
# =============================================================================

# Key under users.metadata where the onboarding checklist state lives. The
# metadata column is a shared JSON blob, so reads must merge-and-write to
# avoid clobbering sibling keys.
_ONBOARDING_METADATA_KEY = "onboarding_progress"


def _read_onboarding(metadata: Optional[dict]) -> OnboardingProgress:
    """Extract OnboardingProgress from a user's metadata dict (or defaults)."""
    raw = (metadata or {}).get(_ONBOARDING_METADATA_KEY) or {}
    if not isinstance(raw, dict):
        raw = {}
    return OnboardingProgress(
        first_agent_created=bool(raw.get("first_agent_created", False)),
        template_applied=bool(raw.get("template_applied", False)),
        dismissed=bool(raw.get("dismissed", False)),
    )


@router.get("/onboarding", response_model=OnboardingResponse)
async def get_onboarding(http_request: Request):
    """Return the authenticated user's onboarding checklist state.

    The frontend calls this on chat-page mount to decide whether to show
    the checklist card and which rows are already checked. Identity comes
    from auth_middleware (was a client-supplied query param).
    """
    user_id = await resolve_current_user_id(http_request)
    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(user_id)
        if not user:
            return OnboardingResponse(success=False, error="User not found")
        return OnboardingResponse(
            success=True,
            progress=_read_onboarding(user.metadata),
        )
    except Exception as e:
        logger.exception(f"Error reading onboarding state: {e}")
        return OnboardingResponse(success=False, error=str(e))


@router.post("/onboarding", response_model=OnboardingResponse)
async def update_onboarding(http_request: Request, request: UpdateOnboardingRequest):
    """Mark one or more onboarding steps complete.

    Write-once-true: only fields explicitly True in the request are
    applied; None / False are ignored so a completed step can never be
    reverted. The merge reads the user's full metadata, updates only the
    `onboarding_progress` sub-key, and writes the whole dict back so
    sibling metadata keys are preserved.
    """
    user_id = await resolve_current_user_id(http_request)
    try:
        db_client = await get_db_client()
        user_repo = UserRepository(db_client)
        user = await user_repo.get_user(user_id)
        if not user:
            return OnboardingResponse(success=False, error="User not found")

        current = _read_onboarding(user.metadata)
        merged = OnboardingProgress(
            first_agent_created=current.first_agent_created
            or request.first_agent_created is True,
            template_applied=current.template_applied
            or request.template_applied is True,
            dismissed=current.dismissed or request.dismissed is True,
        )

        metadata = dict(user.metadata or {})
        metadata[_ONBOARDING_METADATA_KEY] = merged.model_dump()
        await user_repo.update_user(user_id, {"metadata": metadata})

        logger.info(
            f"Onboarding updated for {user_id}: {merged.model_dump()}"
        )
        return OnboardingResponse(success=True, progress=merged)
    except Exception as e:
        logger.exception(f"Error updating onboarding state: {e}")
        return OnboardingResponse(success=False, error=str(e))


# =============================================================================
# Analytics opt-out
# =============================================================================


def _require_request_user(http_request: Request) -> str:
    """Identity for the analytics endpoints comes from auth_middleware
    (request.state.user_id) — never from the query string or body — so one
    user can't read or flip another user's privacy preference."""
    uid = getattr(http_request.state, "user_id", None)
    if not uid:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


class SetAnalyticsOptOutRequest(BaseModel):
    opted_out: bool


@router.get("/settings/analytics")
async def get_analytics_opt_out(http_request: Request):
    """Return whether the current user has opted out of product analytics.

    No-row means not opted out (tracking on by default).
    """
    uid = _require_request_user(http_request)
    repo = UserSettingsRepository(await get_db_client())
    return {"opted_out": await repo.is_analytics_opted_out(uid)}


@router.put("/settings/analytics")
async def set_analytics_opt_out(request: SetAnalyticsOptOutRequest,
                                http_request: Request):
    """Set the current user's analytics opt-out preference."""
    uid = _require_request_user(http_request)
    repo = UserSettingsRepository(await get_db_client())
    await repo.set_analytics_opt_out(uid, request.opted_out)
    return {"success": True, "opted_out": request.opted_out}


class FunnelEventRequest(BaseModel):
    event: str


@router.post("/funnel")
async def track_funnel_event(request: FunnelEventRequest, http_request: Request):
    """Report a frontend-originated funnel event (setup page UI actions).

    Identity comes from auth_middleware (request.state.user_id) — never the
    body — so events can't be spoofed onto another user. Only whitelisted
    setup_* events are accepted, and no client-supplied properties are
    forwarded: the setup_* events carry no payload by design, so accepting a
    properties dict would only let a client inject arbitrary data (or
    override the server-derived `surface`) into PostHog. track() applies
    opt-out, distinct_id hashing, and the surface label, and never raises.
    """
    uid = _require_request_user(http_request)
    if request.event not in _ALLOWED_FUNNEL_EVENTS:
        raise HTTPException(
            status_code=400, detail=f"Unknown funnel event: {request.event}"
        )
    await track(user_id=uid, event=request.event)
    return {"success": True}
