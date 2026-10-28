"""
@file_name: routes.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent Awareness routes

Provides endpoints for:
- GET /{agent_id}/awareness - Get Agent self-awareness
- PUT /{agent_id}/awareness - Update Agent self-awareness
"""

import uuid

from fastapi import APIRouter, Request
from loguru import logger

from narranexus.sdk.web import require_agent_owner
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils import format_for_api
from narranexus.platform.repository import InstanceRepository
from narranexus.platform.repository import InstanceAwarenessRepository
from narranexus.platform.schema import AwarenessResponse, AwarenessUpdateRequest
from narranexus.platform.schema.instance_schema import ModuleInstanceRecord, InstanceStatus


router = APIRouter()


async def _find_awareness_instance(agent_id: str) -> str | None:
    """The agent's AwarenessModule instance_id, or None when it has none."""
    db_client = await get_db_client()
    instance_repo = InstanceRepository(db_client)
    instances = await instance_repo.get_by_agent(
        agent_id=agent_id,
        module_class="AwarenessModule"
    )
    return instances[0].instance_id if instances else None


async def _ensure_awareness_instance(agent_id: str) -> str:
    """
    Ensure an AwarenessModule instance exists for the Agent, create one if not

    Returns:
        instance_id
    """
    existing = await _find_awareness_instance(agent_id)
    if existing:
        return existing
    db_client = await get_db_client()
    instance_repo = InstanceRepository(db_client)

    logger.info(f"No AwarenessModule instance found, creating one for agent: {agent_id}")
    instance_id = f"aware_{uuid.uuid4().hex[:8]}"
    new_instance = ModuleInstanceRecord(
        instance_id=instance_id,
        module_class="AwarenessModule",
        agent_id=agent_id,
        is_public=True,
        status=InstanceStatus.ACTIVE,
        description="Agent self-awareness module instance"
    )
    await instance_repo.create_instance(new_instance)
    logger.info(f"Created new instance: {instance_id}")
    return instance_id


@router.get("/{agent_id}/awareness", response_model=AwarenessResponse)
async def get_agent_awareness(agent_id: str, request: Request):
    """
    Get Agent self-awareness information

    Queries data from instance_awareness table (via AwarenessModule's instance_id).
    Owner-only (cloud mode). A read never creates an instance for the agent:
    an agent with no AwarenessModule instance yet returns not-found, not an
    auto-minted empty one (side-effect-free GET + no instance-spray on lookups).
    """
    logger.debug(f"Getting awareness for agent: {agent_id}")
    await require_agent_owner(request, agent_id)

    try:
        db_client = await get_db_client()
        instance_id = await _find_awareness_instance(agent_id)
        if not instance_id:
            return AwarenessResponse(
                success=False,
                error=f"Awareness data not found for agent: {agent_id}",
            )

        awareness_data = await db_client.get_one(
            "instance_awareness",
            filters={"instance_id": instance_id}
        )

        if awareness_data:
            return AwarenessResponse(
                success=True,
                awareness=awareness_data.get("awareness"),
                create_time=format_for_api(awareness_data.get("created_at")),
                update_time=format_for_api(awareness_data.get("updated_at")),
            )
        else:
            return AwarenessResponse(
                success=False,
                error=f"Awareness data not found for agent: {agent_id}"
            )

    except Exception as e:
        logger.exception(f"Error getting awareness: {e}")
        return AwarenessResponse(success=False, error="Failed to get awareness.")


@router.put("/{agent_id}/awareness", response_model=AwarenessResponse)
async def update_agent_awareness(
    agent_id: str,
    request: AwarenessUpdateRequest,
    http_request: Request,
    create_missing: bool = True,
):
    """
    Update Agent self-awareness information

    Owner-only (cloud mode). Updates data in instance_awareness table (via
    AwarenessModule's instance_id). Creates an AwarenessModule instance
    automatically if none exists — unless ``create_missing=false``: the MCP
    data-access seam (HttpStore) uses that to keep parity with the direct
    path, where an unknown agent_id is an ERROR, not a licence to mint an
    instance for it (the frontend keeps the auto-create default).
    """
    await require_agent_owner(http_request, agent_id)
    logger.info(f"Updating awareness for agent: {agent_id}")
    logger.info(f"  → Request awareness content (first 100 chars): {request.awareness[:100] if request.awareness else 'None'}...")

    try:
        db_client = await get_db_client()
        if create_missing:
            instance_id = await _ensure_awareness_instance(agent_id)
        else:
            instance_id = await _find_awareness_instance(agent_id)
            if not instance_id:
                return AwarenessResponse(
                    success=False,
                    error=f"No AwarenessModule instance found for agent_id={agent_id}",
                )
        logger.info(f"  → Using instance_id: {instance_id}")

        # Update instance_awareness table
        awareness_repo = InstanceAwarenessRepository(db_client)
        # The model rewrites the WHOLE profile through this route, and the format
        # the update_awareness tool prescribes does not include the platform's
        # identity record — so a rewrite deleted the rename correction, putting
        # the agent back to a stale self-name line with nothing contradicting it.
        # Carried over in code rather than requested in the prompt (rule #15).
        # This is the CLOUD half: the twin in module/data_access/store.py does the
        # same for the local path, and fixing only one protects the environment
        # the incident did not happen in.
        from narranexus_plugins.awareness_module import (
            carry_over_platform_record,
        )

        current = await awareness_repo.get_by_instance(instance_id)
        awareness_text = carry_over_platform_record(
            (current.awareness if current else "") or "", request.awareness
        )
        success = await awareness_repo.upsert(instance_id, awareness_text)
        logger.info(f"  → Upsert result: {success}")

        if success:
            awareness_data = await db_client.get_one(
                "instance_awareness",
                filters={"instance_id": instance_id}
            )
            logger.info(f"  → Fetched awareness_data: {awareness_data is not None}")
            if awareness_data:
                logger.info(f"  → Fetched awareness (first 100 chars): {str(awareness_data.get('awareness', ''))[:100]}...")

            return AwarenessResponse(
                success=True,
                awareness=awareness_data.get("awareness") if awareness_data else request.awareness,
                create_time=format_for_api(awareness_data.get("created_at")) if awareness_data else None,
                update_time=format_for_api(awareness_data.get("updated_at")) if awareness_data else None,
            )
        else:
            logger.error(f"  → Upsert failed for instance_id: {instance_id}")
            return AwarenessResponse(success=False, error="Failed to update awareness")

    except Exception as e:
        logger.exception(f"Error updating awareness: {e}")
        return AwarenessResponse(success=False, error="Failed to update awareness.")


# ---- plugin contribution (batch 3c.4): this twin router of the data-access
# seam is provided by builtin.awareness through backend.routes (mounted under /api/agents
# by backend.plugins_host, no longer included by routes/agents/core.py), so the
# HTTP twin disappears together with the capability when the plugin is disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("agents_awareness", lambda: RouterSpec(router, "/api/agents", tags=("Agents",))),)
