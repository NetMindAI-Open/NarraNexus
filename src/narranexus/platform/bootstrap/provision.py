"""
@file_name: provision.py
@author:
@date: 2026-08-10
@description: Canonical new-agent provisioning sequence, extracted as a
shared seam (pre-open review #3 on PR-2).

Before this file, "how a brand-new agent becomes usable" existed as THREE
separate, drifting copies:
  1. `backend/routes/auth.py`'s `create_agent` route — the ORIGINAL,
     complete sequence (semantic source of truth).
  2. `_social_mcp_tools.py`'s `create_agent` MCP tool closure — a half
     copy that skipped default-skill installation entirely, so an
     agent-created-agent had none of the marketplace's default skills.
  3. `backend/routes/agents/social_network.py`'s `create-agent` HTTP route
     — a fuller copy (added in the same PR-2 pre-open review) but still a
     hand-maintained duplicate of (1).

`provision_new_agent` is the single place this sequence now lives. All
THREE call sites delegate to it: auth.py's create_agent route, the
create_agent MCP tool closure, and the social-network create-agent HTTP
route. auth.py remains the SEMANTIC SOURCE (this seam mirrors what its route
established); it keeps only the parts that are NOT the shared sequence —
user-existence validation up front, team assignment (#43) after
provisioning, and building CreateAgentResponse from a re-fetched row.

Sequence (mirrors auth.py's create_agent route as of 2026-08-10):
  0. AgentRepository.add_agent — insert the `agents` row. NOT best-effort:
     a failed insert (duplicate id, DB down) means there is no agent to
     provision, so it propagates and aborts the whole call
  1. InstanceFactory.create_agent_level_instances — Awareness/Social/
     BasicInfo/MessageBus/Lark/HomeAssistant instances
  2. sync_agent_discovery — peer-discovery registration
  3. apply_bootstrap(profile) — Bootstrap.md + greeting + auto-delete rule
  4. SkillMarketplaceService.install_defaults — fire-and-forget default
     skills (the step pre-open review #3 flagged as missing from the MCP
     tool copy)
  5. seed the caller-supplied `awareness` text onto the AwarenessModule
     instance from step 1, if the caller passed one
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional
from uuid import uuid4

from loguru import logger

from narranexus.platform.message_bus.agent_discovery_sync import sync_agent_discovery
from narranexus.platform.repository import AgentRepository

# agent_id is used as a filesystem PATH SEGMENT (agent_workspace_path builds
# base/{user_id}/{agent_id}) and as a DB key. It must be a safe token — a value
# like "../other_user/agent" would traverse into another tenant's workspace.
# This is the ONE provisioning seam, so validating here backstops every call
# site (auth route, the create_agent MCP tool via DirectStore, the social
# create-agent route) at once — 铁律 #5, fix the root cause not one entry point.
_SAFE_AGENT_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


@dataclass
class ProvisionResult:
    """Outcome of provisioning a brand-new agent's default state.

    `bootstrap_active` mirrors auth.py's historical semantics: True only
    when the bootstrap profile rendered a Bootstrap.md AND that file still
    exists on disk right after `apply_bootstrap` ran. Every step past the
    initial `add_agent` insert is best-effort and folds its failure into
    `warnings` rather than raising — EXCEPT peer-discovery (step 2), which is
    deliberately bare so its failure bubbles to the caller's own try (matching
    auth.py's historical behaviour). A partially-provisioned agent is still a
    created agent.
    """

    agent_id: str
    bootstrap_active: bool = False
    warnings: list[str] = field(default_factory=list)


async def provision_new_agent(
    db: Any,
    *,
    agent_id: str,
    user_id: str,
    agent_name: str,
    agent_description: str = "",
    awareness: Optional[str] = None,
    bootstrap_profile: str = "default",
    bootstrap_ctx_extra: Optional[dict[str, Any]] = None,
) -> ProvisionResult:
    """
    Provision a brand-new agent: insert its `agents` row, create its default
    module instances, register it for peer discovery, run its first-run
    bootstrap flow, install its default skills, and (optionally) seed an
    initial awareness text. See the module docstring for the full sequence
    and how all three call sites converge on it.

    Args:
        db: Database client
        agent_id: Pre-generated agent id (callers own id generation because
            they differ in how they resolve the owning user_id)
        user_id: The agent's owner (`agents.created_by`)
        agent_name: Display name for the new agent
        agent_description: Optional short description
        awareness: Optional self-awareness text to seed. None = skip step 5
            entirely (some callers, like the MCP `create_agent` tool,
            require the caller to always supply one; others may not)
        bootstrap_profile: Bootstrap profile name (see
            `narranexus.platform.bootstrap.profiles.get_profile`)
        bootstrap_ctx_extra: Scenario params forwarded as
            `BootstrapContext.extra` to the profile's render methods (e.g.
            the onboarding profile's persona_key/topic_index/is_local).
            None = empty extra. Added so scenario callers don't have to
            provision with profile "none" and re-apply their real profile
            afterwards (which briefly persisted a blank greeting and turned
            a failed re-apply into a permanently mute agent).

    Returns:
        ProvisionResult — bootstrap_active plus any non-fatal warnings
        collected along the way.
    """
    warnings: list[str] = []

    # Reject an unsafe agent_id before it becomes a path segment or DB key (see
    # _SAFE_AGENT_ID). Raised, not folded into warnings: a bad id means we must
    # NOT create anything (no row, no workspace dir), so it aborts the call.
    # fullmatch (not match): `$` would accept a trailing newline, and this is a
    # security primitive whose whole job is to catch a bad id from any caller.
    if not _SAFE_AGENT_ID.fullmatch(agent_id):
        raise ValueError(f"unsafe agent_id (must match [A-Za-z0-9_-]+): {agent_id!r}")

    # 0. Insert the agent row itself. Not wrapped in try/except — a failed
    # insert (duplicate id, DB down) means there is no agent to provision,
    # so it must propagate and abort the whole call.
    await AgentRepository(db).add_agent(
        agent_id=agent_id,
        agent_name=agent_name,
        created_by=user_id,
        agent_description=agent_description,
        agent_type="chat",
    )
    logger.info(f"[provision] agent row created: {agent_id} (owner={user_id})")

    # 1. Default agent-level module instances (Awareness/SocialNetwork/
    # BasicInfo/MessageBus/Lark/HomeAssistant). Idempotent; best-effort so a
    # transient failure never blocks the rest of provisioning — the agent
    # row already exists either way.
    try:
        from narranexus.platform.module_system._module_impl.instance_factory import InstanceFactory

        await InstanceFactory(db).create_agent_level_instances(agent_id)
    except Exception as inst_err:  # noqa: BLE001
        logger.warning(f"[provision] instance factory failed for {agent_id}: {inst_err}")
        warnings.append(f"instance_factory: {inst_err}")

    # 2. Peer-discovery registration. Deliberately BARE (no try/except) —
    # this mirrors auth.py's create_agent, which has never wrapped this
    # call either. A failure here propagates out of provision_new_agent;
    # each caller's own outer error handling decides what that means for
    # the request (today: the whole creation call reports failure even
    # though the agent row already exists — existing, load-bearing
    # behavior, not changed by this extraction. See auth.py's 2026-08-04
    # mirror entry on why immediate peer visibility matters enough that a
    # silent swallow here would hide a real problem).
    await sync_agent_discovery(db, agent_id)

    # 3. Bootstrap profile — renders Bootstrap.md + greeting + auto-delete
    # rule. Best-effort: the agent row already exists either way.
    bootstrap_active = False
    try:
        import os

        from narranexus.platform.bootstrap.profiles import (
            BootstrapContext,
            apply_bootstrap,
            get_profile,
        )
        from narranexus.platform.settings import settings
        from narranexus.platform.utils.workspace_paths import agent_workspace_path

        profile = get_profile(bootstrap_profile)
        await apply_bootstrap(
            db,
            agent_id=agent_id,
            user_id=user_id,
            profile=profile,
            ctx=BootstrapContext(
                agent_id=agent_id,
                user_id=user_id,
                agent_name=agent_name,
                extra=dict(bootstrap_ctx_extra or {}),
            ),
        )
        workspace_path = agent_workspace_path(agent_id, user_id, base=settings.base_working_path)
        bootstrap_active = os.path.isfile(str(workspace_path / "Bootstrap.md"))
        logger.info(f"[provision] bootstrap profile '{profile.name}' applied to {agent_id}")
    except Exception as bootstrap_err:  # noqa: BLE001
        logger.warning(f"[provision] bootstrap profile failed for {agent_id}: {bootstrap_err}")
        warnings.append(f"bootstrap: {bootstrap_err}")

    # 4. Default skills — fire-and-forget install of every marketplace skill
    # flagged is_default (pre-open review #3: this step was MISSING from
    # every provisioning path except auth.py's, so agents created via the
    # MCP tool or the HTTP route had none of their default skills). Never
    # blocks or fails creation: an unreachable registry degrades to a no-op
    # inside the service.
    try:
        import asyncio as _asyncio

        from narranexus.platform.marketplace.skill_marketplace_service import SkillMarketplaceService

        async def _install_default_skills(aid: str, uid: str) -> None:
            try:
                summary = await SkillMarketplaceService().install_defaults(aid, uid)
                if summary.get("failed"):
                    logger.warning(f"[provision] default skills for {aid}: failed={summary['failed']}")
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"[provision] default skills install for {aid} skipped: {exc}")

        _task = _asyncio.create_task(_install_default_skills(agent_id, user_id))
        # Fire-and-forget needs a done callback (incident lesson #2); the
        # inner try/except already swallows expected failures, this catches
        # cancellation-adjacent surprises.
        _task.add_done_callback(
            lambda t: (
                logger.warning(f"[provision] default-skills task died: {t.exception()}")
                if not t.cancelled() and t.exception() is not None
                else None
            )
        )
    except Exception as defaults_err:  # noqa: BLE001
        logger.warning(f"[provision] failed to schedule default skills for {agent_id}: {defaults_err}")
        warnings.append(f"default_skills: {defaults_err}")

    # 5. Seed the caller-supplied awareness text onto the awareness module's
    # instance step 1 created (created from the module's declaration if step 1
    # failed to). Best-effort; only runs when the caller passed `awareness`.
    if awareness is not None:
        try:
            from narranexus.platform.module_system import InstanceFactory
            from narranexus.platform.repository import InstanceAwarenessRepository

            awareness_instance = await InstanceFactory(db).ensure_role_instance(agent_id, "awareness")
            if awareness_instance is None:
                raise RuntimeError("no module declares the 'awareness' role")
            awareness_instance_id = awareness_instance.instance_id
            await InstanceAwarenessRepository(db).upsert(awareness_instance_id, awareness)
            logger.info(f"[provision] set awareness for {agent_id}: {len(awareness)} chars")
        except Exception as awareness_err:  # noqa: BLE001
            logger.warning(f"[provision] failed to seed awareness for {agent_id}: {awareness_err}")
            warnings.append(f"awareness: {awareness_err}")

    return ProvisionResult(agent_id=agent_id, bootstrap_active=bootstrap_active, warnings=warnings)
