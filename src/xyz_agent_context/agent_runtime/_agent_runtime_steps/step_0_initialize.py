"""
@file_name: step_0_initialize.py
@author: NetMind.AI
@date: 2025-12-24
@description: Step 0 - Initialization phase

Unified initialization logic:
- 0.1 Get Agent configuration
- 0.2 Initialize ModuleService
- 0.3 Create Event record
- 0.4 Get/Create Session
- 0.5 Get Agent Awareness
"""

from __future__ import annotations

from typing import AsyncGenerator, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.utils.logging import timed

from xyz_agent_context.schema import ProgressMessage, ProgressStatus
from xyz_agent_context.repository import AgentRepository, InstanceRepository, InstanceAwarenessRepository
from xyz_agent_context.module import ModuleService

if TYPE_CHECKING:
    from .context import RunContext
    from xyz_agent_context.utils import DatabaseClient
    from xyz_agent_context.narrative import EventService, SessionService


@timed("step.0_initialize")

async def step_0_initialize(
    ctx: "RunContext",
    db_client: "DatabaseClient",
    event_service: "EventService",
    session_service: "SessionService"
) -> AsyncGenerator[ProgressMessage, None]:
    """
    Step 0: Initialization phase

    Execute all initialization work:
    1. Get Agent configuration (from database)
    2. Initialize ModuleService (prepare module loader)
    3. Create Event record (carrier for this conversation)
    4. Get/Create Session (manage session continuity)
    5. Get Agent Awareness (load self-awareness from instance_awareness table)

    Args:
        ctx: Run context
        db_client: Database client
        event_service: Event service
        session_service: Session service

    Yields:
        ProgressMessage: Progress messages
    """
    yield ProgressMessage(
        step="0",
        title="Initialization",
        description="Get config, create Event, initialize Session",
        status=ProgressStatus.RUNNING,
        substeps=ctx.substeps_0
    )

    # =========================================================================
    # 0.1 Get Agent configuration
    # =========================================================================
    agent_repo = AgentRepository(db_client)
    agent = await agent_repo.get_agent(ctx.agent_id)
    if agent is None:
        logger.error(f"Agent {ctx.agent_id} not found in database")
        raise ValueError(f"Agent {ctx.agent_id} not found")

    ctx.agent_data = {
        "agent_id": agent.agent_id,
        "agent_name": agent.agent_name,
        "agent_type": agent.agent_type,
        "agent_description": agent.agent_description,
        "agent_metadata": agent.agent_metadata,
        "created_by": agent.created_by,
    }
    ctx.substeps_0.append(
        f"[0.1] ✓ Agent: {agent.agent_name or 'Unknown'} ({agent.agent_type or 'Unknown'})"
    )
    logger.info(f"Agent data fetched: {agent.agent_name}")

    # =========================================================================
    # 0.2 Initialize ModuleService
    # =========================================================================
    ctx.module_service = ModuleService(ctx.agent_id, ctx.user_id, db_client)
    ctx.substeps_0.append("[0.2] ✓ ModuleService ready")
    logger.debug("ModuleService initialized")

    # =========================================================================
    # 0.3 Create Event record
    # =========================================================================
    event = await event_service.create_event(
        ctx.agent_id, ctx.user_id, ctx.input_content
    )
    ctx.event = event
    ctx.substeps_0.append(f"[0.3] ✓ Event: {event.id}")
    logger.info(f"Event created: event_id={event.id}")

    # =========================================================================
    # 0.4 Get/Create Session
    # =========================================================================
    session = await session_service.get_or_create_session(
        ctx.user_id, ctx.agent_id
    )
    ctx.session = session
    ctx.substeps_0.append(f"[0.4] ✓ Session: {session.session_id} (queries: {session.query_count})")
    logger.info(f"Session ready: {session.session_id}")

    # =========================================================================
    # 0.5 Get Agent Awareness
    # TODO: [Duplicate reads] The ctx.awareness preloaded here and the ctx_data.awareness
    #       set in AwarenessModule.hook_data_gathering() are two different objects. Currently
    #       reads from database twice.
    #       Future optimization: pass ctx.awareness to ContextRuntime to avoid AwarenessModule querying again.
    # =========================================================================
    awareness = ""
    try:
        # Find the AwarenessModule instance_id via agent_id + module_class
        instance_repo = InstanceRepository(db_client)
        instances = await instance_repo.get_by_agent(
            agent_id=ctx.agent_id,
            module_class="AwarenessModule"
        )

        if instances:
            instance_id = instances[0].instance_id
            # Get awareness content via instance_id
            awareness_repo = InstanceAwarenessRepository(db_client)
            awareness_entity = await awareness_repo.get_by_instance(instance_id)
            if awareness_entity:
                awareness = awareness_entity.awareness
                logger.debug(f"    Awareness loaded: {awareness[:50]}..." if len(awareness) > 50 else f"    Awareness loaded: {awareness}")
    except Exception as e:
        logger.warning(f"Failed to get awareness: {e}")

    ctx.awareness = awareness
    ctx.substeps_0.append(f"[0.5] ✓ Awareness: {'loaded' if awareness else '(empty)'}")
    logger.info(f"Awareness ready: {'loaded' if awareness else 'empty'}")

    # =========================================================================
    # Complete
    # =========================================================================
    yield ProgressMessage(
        step="0",
        title="Initialization",
        description=f"✓ Agent={agent.agent_name}, Event={event.id}",
        status=ProgressStatus.COMPLETED,
        details={
            "agent_name": agent.agent_name,
            "event_id": event.id,
            "session_id": session.session_id,
        },
        substeps=ctx.substeps_0
    )
