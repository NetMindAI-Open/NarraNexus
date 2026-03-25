"""
@file_name: step_1_select_narrative.py
@author: NetMind.AI
@date: 2025-12-22
@description: Step 1 - Select Narrative

Select the appropriate memory narrative based on Session + continuity detection + vector matching.

Changelog:
- 2026-01-19: Added _ensure_user_chat_instance to ensure users have a corresponding ChatModule instance in the Narrative
"""

from __future__ import annotations

import asyncio
from typing import AsyncGenerator, Dict, TYPE_CHECKING

from loguru import logger
from xyz_agent_context.agent_runtime.cancellation import CancellationToken, CancelledByUser


async def _run_with_cancellation(coro, cancellation: CancellationToken):
    """
    Run a coroutine that can be interrupted by a CancellationToken.

    If the token fires while the coroutine is awaiting (e.g., an LLM call),
    the task is cancelled immediately via asyncio.Task.cancel(), so the user
    doesn't have to wait for a long operation to finish.
    """
    task = asyncio.ensure_future(coro)

    # Wait for cancellation event
    cancel_waiter = asyncio.ensure_future(cancellation._event.wait())

    done, pending = await asyncio.wait(
        [task, cancel_waiter],
        return_when=asyncio.FIRST_COMPLETED,
    )

    if cancel_waiter in done:
        # Cancellation fired — abort the task
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
        cancel_waiter.cancel()
        raise CancelledByUser(cancellation.reason)

    # Task finished normally — clean up the cancel waiter
    cancel_waiter.cancel()
    return task.result()

from xyz_agent_context.schema import ProgressMessage, ProgressStatus

from .step_display import format_narrative_for_display

if TYPE_CHECKING:
    from .context import RunContext
    from xyz_agent_context.narrative import NarrativeService, SessionService


async def _ensure_user_chat_instance(
    agent_id: str,
    user_id: str,
    narrative_id: str
) -> str:
    """
    Ensure a user has a corresponding ChatModule instance in the specified Narrative.

    Sales scenario support: when different users (e.g., target customers) interact with the Agent
    in the same Narrative, an independent ChatModule instance must be created for each user
    to track their respective chat histories.

    Flow:
    1. Query whether a ChatModule instance exists for this user in this Narrative
    2. If not, create a new ChatModule instance and associate it with the Narrative

    Args:
        agent_id: Agent ID
        user_id: User ID (could be a sales manager or a target customer)
        narrative_id: Narrative ID

    Returns:
        str: ChatModule instance ID
    """
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils import utc_now
    from xyz_agent_context.repository import InstanceRepository, InstanceNarrativeLinkRepository
    from xyz_agent_context.schema.instance_schema import ModuleInstanceRecord, InstanceStatus, LinkType
    from xyz_agent_context.module import generate_instance_id

    db_client = await get_db_client()
    instance_repo = InstanceRepository(db_client)
    link_repo = InstanceNarrativeLinkRepository(db_client)

    # 1. Query ChatModule instances associated with the current user in this Narrative
    # Get all active instance IDs associated with the Narrative
    linked_instance_ids = await link_repo.get_instances_for_narrative(
        narrative_id, link_type=LinkType.ACTIVE
    )

    # Find ChatModule instances belonging to the current user
    user_chat_instance_id = None
    for inst_id in linked_instance_ids:
        instance = await instance_repo.get_by_instance_id(inst_id)
        if instance and instance.module_class == "ChatModule" and instance.user_id == user_id:
            user_chat_instance_id = instance.instance_id
            logger.debug(
                f"Found user {user_id}'s ChatModule instance in Narrative {narrative_id}: {inst_id}"
            )
            break

    if user_chat_instance_id:
        return user_chat_instance_id

    # 2. Does not exist, create a new ChatModule instance
    logger.info(
        f"User {user_id} has no ChatModule instance in Narrative {narrative_id}, creating..."
    )

    new_instance_id = generate_instance_id("chat")
    instance = ModuleInstanceRecord(
        instance_id=new_instance_id,
        module_class="ChatModule",
        agent_id=agent_id,
        user_id=user_id,
        is_public=False,
        status=InstanceStatus.ACTIVE,
        description=f"Chat instance for user {user_id}",
        keywords=["chat", "conversation", "dialogue"],
        topic_hint="Chat interactions and message history",
        created_at=utc_now(),
    )

    await instance_repo.create_instance(instance)
    logger.info(f"Created ChatModule instance: {new_instance_id} for user {user_id}")

    # 3. Establish association with the Narrative
    await link_repo.link(new_instance_id, narrative_id, link_type=LinkType.ACTIVE)
    logger.info(f"Linked ChatModule instance {new_instance_id} to Narrative {narrative_id}")

    return new_instance_id


async def step_1_select_narrative(
    ctx: "RunContext",
    narrative_service: "NarrativeService",
    session_service: "SessionService",
) -> AsyncGenerator[ProgressMessage, None]:
    """
    Step 1: Select Narrative

    Select the most appropriate Narrative based on multiple strategies.

    Args:
        ctx: Run context
        narrative_service: Narrative service
        session_service: Session service

    Yields:
        ProgressMessage: Progress messages
    """
    # Send Running status
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection",
        description="Querying vector store for relevant narratives...",
        status=ProgressStatus.RUNNING,
        substeps=[]
    )

    logger.info("🎯 Step 1: Selecting Narratives")

    # Cancellation checkpoint — abort before any work if already cancelled
    if ctx.cancellation:
        ctx.cancellation.raise_if_cancelled()

    # ========== Check if there is a forced Narrative (used for Job triggers) ==========
    is_new = False  # Default to not newly created
    retrieval_method = ""  # Default empty, will be set in subsequent branches
    if ctx.forced_narrative_id:
        logger.info(f"🔒 Using forced Narrative: {ctx.forced_narrative_id}")
        # Directly load the specified Narrative, skip search
        forced_narrative = await narrative_service.load_narrative_from_db(ctx.forced_narrative_id)
        if forced_narrative:
            narrative_list = [forced_narrative]
            query_embedding = None
            selection_reason = f"Forced Narrative (Job trigger): {ctx.forced_narrative_id}"
            selection_method = "forced"
            retrieval_method = "forced"  # Forced, no retrieval
            is_new = False  # Forced Narrative is not newly created
            logger.success(f"✅ Successfully loaded forced Narrative: {forced_narrative.id}")
        else:
            logger.warning(f"⚠️ Forced Narrative {ctx.forced_narrative_id} does not exist, falling back to normal selection")
            # Fall back to normal selection flow
            fallback_coro = narrative_service.select(
                ctx.agent_id, ctx.user_id, ctx.input_content,
                session=ctx.session,
                awareness=ctx.awareness
            )
            if ctx.cancellation:
                selection_result = await _run_with_cancellation(fallback_coro, ctx.cancellation)
            else:
                selection_result = await fallback_coro
            narrative_list = selection_result.narratives
            query_embedding = selection_result.query_embedding
            selection_reason = selection_result.selection_reason
            selection_method = selection_result.selection_method
            is_new = selection_result.is_new
            retrieval_method = selection_result.retrieval_method
    else:
        # ========== Normal Narrative selection flow ==========
        # Wrap select() so cancellation can interrupt LLM/embedding calls mid-flight
        select_coro = narrative_service.select(
            ctx.agent_id, ctx.user_id, ctx.input_content,
            session=ctx.session,
            awareness=ctx.awareness
        )
        if ctx.cancellation:
            selection_result = await _run_with_cancellation(select_coro, ctx.cancellation)
        else:
            selection_result = await select_coro
        narrative_list = selection_result.narratives
        query_embedding = selection_result.query_embedding
        selection_reason = selection_result.selection_reason
        selection_method = selection_result.selection_method
        is_new = selection_result.is_new
        retrieval_method = selection_result.retrieval_method

        # Phase 2: Cache EverMemOS retrieval results for MemoryModule use
        if selection_result.evermemos_memories:
            ctx.evermemos_memories = selection_result.evermemos_memories
            logger.debug(
                f"[Phase 2] Cached evermemos_memories: {len(ctx.evermemos_memories)} Narratives"
            )

    # Cancellation checkpoint — abort after selection before post-processing
    if ctx.cancellation:
        ctx.cancellation.raise_if_cancelled()

    ctx.narrative_list = narrative_list
    ctx.query_embedding = query_embedding

    logger.success(
        f"✅ Narratives selected: count={len(narrative_list)}, "
        f"method={selection_method}, reason={selection_reason[:50]}..."
    )

    # Use similarity scores already computed during Narrative selection
    # (carried through NarrativeSelectionResult.scores, avoiding redundant
    # embedding loading and cosine similarity re-computation)
    scores: Dict[str, float] = selection_result.scores

    # Format for developer-friendly display
    display_data = format_narrative_for_display(narrative_list, scores=scores)

    # Generate substeps (including scores)
    substeps = []
    for item in display_data["items"]:
        score_str = f" score={item['score']}" if 'score' in item else ""
        substeps.append(f"{item['id']}{score_str} ({item['time']})")

    # Developer logs
    for i, narrative in enumerate(narrative_list):
        summary_preview = (
            narrative.narrative_info.current_summary
            if narrative.narrative_info.current_summary
            else 'None'
        )
        logger.info(f"  📖 Narrative[{i}]: id={narrative.id}, summary={summary_preview}...")
        ctx.substeps_1.append(f"[1.{i+1}] ✓ {narrative.narrative_info.name}")

    # Ensure the current user has a ChatModule instance in each selected Narrative
    # Sales scenario support: different users (sales managers/target customers) need independent chat records in the same Narrative
    user_chat_instances = {}
    for narrative in narrative_list:
        try:
            chat_instance_id = await _ensure_user_chat_instance(
                ctx.agent_id, ctx.user_id, narrative.id
            )
            user_chat_instances[narrative.id] = chat_instance_id
            logger.debug(f"User {ctx.user_id} ChatModule instance: {chat_instance_id} (Narrative: {narrative.id})")
        except Exception as e:
            logger.warning(f"Failed to ensure ChatModule instance (Narrative: {narrative.id}): {e}")

    # Store in ctx for subsequent steps
    ctx.user_chat_instances = user_chat_instances

    # Persist Session update
    await session_service.save_session(ctx.session)
    logger.debug(f"✅ Session persisted: {ctx.session.session_id}")

    # Send Completed status
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection",
        description=display_data["summary"],
        status=ProgressStatus.COMPLETED,
        details={
            "display": display_data,
            "query_embedding": "generated" if ctx.query_embedding else "none",
            "selection_reason": selection_reason,
            "selection_method": selection_method,
            "retrieval_method": retrieval_method,  # Retrieval method: evermemos, vector, fallback_vector, forced
            "is_new": is_new,
        },
        substeps=substeps if substeps else ["No narratives matched"]
    )
