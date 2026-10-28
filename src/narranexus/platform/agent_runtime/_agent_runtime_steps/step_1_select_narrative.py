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
from narranexus.platform.utils.logging import timed
from narranexus.platform.agent_runtime.cancellation import CancellationToken, CancelledByUser


def _hooks(ctx):
    """Hook registry for this run: the runtime's registries when injected, else the process kernel registries."""
    regs = getattr(ctx, "registries", None)
    if regs is None:
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        regs = KERNEL_REGISTRIES
    return regs.hooks



def _is_user_chat(ctx) -> bool:
    """True iff the current run is replying to a human (not a background
    trigger or peer agent).

    Delegates to `WorkingSource.is_from_human()`:
      - True for CHAT / LARK / SLACK / TELEGRAM (any human-facing channel).
      - False for JOB / MESSAGE_BUS / CALLBACK / SKILL_STUDY.

    Only "True" runs are allowed to overwrite Session.last_query /
    last_response — otherwise a cron job or peer-agent ping would clobber
    the anchor a real user message expects to see on its next continuity
    check.
    """
    from narranexus.platform.schema.hook_schema import WorkingSource

    src = getattr(ctx, "working_source", None)
    if src is None:
        return True   # unknown source → treat as human (legacy safe default)
    if isinstance(src, WorkingSource):
        return src.is_from_human()
    try:
        return WorkingSource(str(src)).is_from_human()
    except ValueError:
        # Unknown literal — be safe and treat as human (preserves Session
        # updates if a new source gets added without updating the enum).
        return True


def _trigger_label(ctx) -> str:
    """The surface that started this run, for the routing audit.

    Recorded because the two dominant surfaces route under different rules:
    on dev (2026-08-07) chat is 69% of turns and message_bus 30%, and only
    the human-facing ones move the session anchor (see `_is_user_chat`). An
    audit that mixed them would average two different behaviours into one
    meaningless rate.
    """
    src = getattr(ctx, "working_source", None)
    if src is None:
        return ""
    return src.value if hasattr(src, "value") else str(src)


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

from narranexus.platform.schema import ProgressMessage, ProgressStatus

from .step_display import format_narrative_for_display

if TYPE_CHECKING:
    from .context import RunContext
    from narranexus.platform.narrative import NarrativeService, SessionService


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
    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils import utc_now
    from narranexus.platform.repository import InstanceRepository, InstanceNarrativeLinkRepository
    from narranexus.platform.schema.instance_schema import ModuleInstanceRecord, InstanceStatus, LinkType
    from narranexus.platform.module_system import generate_instance_id

    db_client = await get_db_client()
    instance_repo = InstanceRepository(db_client)
    link_repo = InstanceNarrativeLinkRepository(db_client)

    # 1. Query ChatModule instances associated with the current user in this Narrative
    # Get all active instance IDs associated with the Narrative
    linked_instance_ids = await link_repo.get_instances_for_narrative(
        narrative_id, link_type=LinkType.ACTIVE
    )

    # Find the current user's chat-history instance in this narrative.
    from narranexus.platform.module_system import module_class_provides_chat_history
    user_chat_instance_id = None
    for inst_id in linked_instance_ids:
        instance = await instance_repo.get_by_instance_id(inst_id)
        if (
            instance
            and module_class_provides_chat_history(instance.module_class)
            and instance.user_id == user_id
        ):
            user_chat_instance_id = instance.instance_id
            logger.debug(
                f"Found user {user_id}'s chat instance in Narrative {narrative_id}: {inst_id}"
            )
            break

    if user_chat_instance_id:
        return user_chat_instance_id

    # 2. Does not exist, create a new ChatModule instance
    logger.info(
        f"User {user_id} has no ChatModule instance in Narrative {narrative_id}, creating..."
    )

    from narranexus.platform.module_system import instance_prefix_for, module_by_role

    chat_module = module_by_role("chat")
    if chat_module is None:
        raise RuntimeError("no registered module declares the 'chat' role")
    new_instance_id = generate_instance_id(instance_prefix_for(chat_module))
    instance = ModuleInstanceRecord(
        instance_id=new_instance_id,
        module_class=chat_module,
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


@timed("step.1_select_narrative")

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
        description="Searching narratives (keyword)...",
        status=ProgressStatus.RUNNING,
        substeps=[]
    )

    # Cancellation checkpoint — abort before any work if already cancelled
    if ctx.cancellation:
        ctx.cancellation.raise_if_cancelled()

    # ========== Check if there is a forced Narrative (used for Job triggers) ==========
    selection_result = None  # Set when going through select() path; None when forced
    is_new = False  # Default to not newly created
    retrieval_method = ""  # Default empty, will be set in subsequent branches
    if ctx.forced_narrative_id:
        logger.info(f"🔒 Using forced Narrative: {ctx.forced_narrative_id}")
        # Directly load the specified Narrative, skip search
        forced_narrative = await narrative_service.load_narrative_from_db(ctx.forced_narrative_id)
        if forced_narrative:
            narrative_list = [forced_narrative]
            selection_reason = f"Forced Narrative (Job trigger): {ctx.forced_narrative_id}"
            selection_method = "forced"
            retrieval_method = "forced"  # Forced, no retrieval
            is_new = False  # Forced Narrative is not newly created
            logger.info(f"Successfully loaded forced Narrative: {forced_narrative.id}")
        else:
            logger.warning(f"Forced Narrative {ctx.forced_narrative_id} does not exist, falling back to normal selection")
            # Fall back to normal selection flow
            fallback_coro = narrative_service.select(
                ctx.agent_id, ctx.user_id, ctx.input_content,
                session=ctx.session,
                awareness=ctx.awareness,
                is_user_chat=_is_user_chat(ctx),
                retrieval_anchor=ctx.trigger_extra_data.get("retrieval_anchor"),
                trigger=_trigger_label(ctx),
            )
            if ctx.cancellation:
                selection_result = await _run_with_cancellation(fallback_coro, ctx.cancellation)
            else:
                selection_result = await fallback_coro
            narrative_list = selection_result.narratives
            selection_reason = selection_result.selection_reason
            selection_method = selection_result.selection_method
            is_new = selection_result.is_new
            retrieval_method = selection_result.retrieval_method
    else:
        # ========== Normal Narrative selection flow ==========
        # Wrap select() so cancellation can interrupt LLM calls mid-flight
        select_coro = narrative_service.select(
            ctx.agent_id, ctx.user_id, ctx.input_content,
            session=ctx.session,
            awareness=ctx.awareness,
            is_user_chat=_is_user_chat(ctx),
            retrieval_anchor=ctx.trigger_extra_data.get("retrieval_anchor"),
            trigger=_trigger_label(ctx),
        )
        if ctx.cancellation:
            selection_result = await _run_with_cancellation(select_coro, ctx.cancellation)
        else:
            selection_result = await select_coro
        narrative_list = selection_result.narratives
        selection_reason = selection_result.selection_reason
        selection_method = selection_result.selection_method
        is_new = selection_result.is_new
        retrieval_method = selection_result.retrieval_method

    # Cancellation checkpoint — abort after selection before post-processing
    if ctx.cancellation:
        ctx.cancellation.raise_if_cancelled()

    ctx.narrative_list = narrative_list
    ctx.no_durable_topic = bool(
        selection_result is not None and selection_result.no_durable_topic
    )

    logger.info(
        f"Narratives selected: count={len(narrative_list)}, "
        f"method={selection_method}, reason={selection_reason[:50]}..."
    )

    # Use similarity scores already computed during Narrative selection
    # (carried through NarrativeSelectionResult.scores, avoiding redundant
    # BM25 score re-computation).
    # Forced Narrative path skips selection — no scores available.
    scores: Dict[str, float] = selection_result.scores if selection_result else {}

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
        logger.info(f"Narrative[{i}]: id={narrative.id}, summary={summary_preview}...")
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

    # Seed the bootstrap greeting into the HEAD narrative's chat instance, ONCE
    # per turn. The greeting's scope is (agent, user) — not per-narrative — so it
    # goes only to narrative_list[0], the authored/primary thread and the very
    # instance ChatModule.persist_turn persists into (the rest are BM25
    # read-side neighbours). Doing it here, at the start of the first turn, means
    # the greeting lands even if the turn never reaches the persist hook.
    #
    # Timestamp is anchored to turn start (ctx.event.created_at) by the writer,
    # so it sorts before the user's first message and satisfies the TIME half of
    # the frontend session-copy dedup window (content-equality is the other half,
    # which does not hold in non-English UIs — see _chat_writes docstring); the
    # write is idempotent (skips a non-empty instance), so the
    # hook's own prepend — which then sees a non-empty history and skips — never
    # doubles it. resolve_bootstrap_greeting_to_seed gates on the SAME
    # bootstrap_active signal the hook uses (Bootstrap.md present + under the
    # auto-delete threshold), so an agent past its bootstrap phase is never
    # re-greeted on a freshly-opened narrative. Only this main step_1 path seeds;
    # the fast-select and step_4 paths rely on the hook prepend as the fallback.
    #
    # Known edge (accepted): if the agent ROUTES to a different narrative on this
    # same bootstrapping turn (step_4 create/switch_narrative rebinds the persist
    # instance), the hook then greets the routed instance too — a greeting-only
    # orphan in narrative_list[0]. The standard bootstrap flow (set name/awareness,
    # no topic creation) does not route on turn 1, so this stays rare.
    # Best-effort.
    if narrative_list and ctx.event is not None:
        head_instance = user_chat_instances.get(narrative_list[0].id)
        if head_instance:
            try:
                from narranexus.platform.bootstrap.greeting_seed import (
                    resolve_bootstrap_greeting_to_seed,
                )
                from narranexus.platform.utils.db.db_factory import get_db_client

                seed_db = await get_db_client()
                greeting = await resolve_bootstrap_greeting_to_seed(
                    seed_db, ctx.agent_id, ctx.user_id
                )
                if greeting:
                    # The platform owns no chat history: builtin.chat implements
                    # this hook (module/chat_module/plugin_hooks.py) and seeds
                    # idempotently; with chat disabled nothing listens.
                    await _hooks(ctx).caller("onDidResolveBootstrapGreeting").call(
                        agent_id=ctx.agent_id,
                        user_id=ctx.user_id,
                        instance_id=head_instance,
                        greeting=greeting,
                        turn_started_at=ctx.event.created_at,
                    )
            except Exception as e:  # noqa: BLE001 — best-effort; hook is the fallback
                logger.warning(f"[bootstrap] greeting seed skipped: {e}")

    # Persist Session update
    await session_service.save_session(ctx.session)
    logger.debug(f"Session persisted: {ctx.session.session_id}")

    # Send Completed status
    yield ProgressMessage(
        step="1",
        title="📚 Narrative Selection",
        description=display_data["summary"],
        status=ProgressStatus.COMPLETED,
        details={
            "display": display_data,
            "selection_reason": selection_reason,
            "selection_method": selection_method,
            "retrieval_method": retrieval_method,  # keyword (BM25) / session (continuity) / forced
            "is_new": is_new,
        },
        substeps=substeps if substeps else ["No narratives matched"]
    )
