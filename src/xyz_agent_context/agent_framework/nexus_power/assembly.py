"""
@file_name: assembly.py
@author: Bin Liang
@date: 2026-07-29
@description: The single wiring point — TurnRequest in, a typed event
stream out.

``LoopAssembly`` is the loop's complete dependency set ("adding a
component = adding a field with a default"); ``build_assembly`` is the
only default construction site — every wiring decision lives here and
nowhere else. Tests replace any component with ``dataclasses.replace``;
nothing is ever monkey-patched. The whole request is JSON-serializable
(the standalone-process runner's transport precondition).

R1 decision on growth: assembly complexity is deliberately concentrated
in this one file; strategy seams default, hard components are wired
per-turn.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from xyz_agent_context.agent_framework.nexus_power.contracts.events import LoopEvent
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    McpServerSpec,
    ModelParams,
    ProviderMessage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.options import TurnOptions
from xyz_agent_context.agent_framework.nexus_power.contracts.protocols import (
    CancellationSignal,
    CompactionPolicy,
    ContextProjector,
    ErrorClassifier,
    EventLogWriter,
    ExpressionPolicy,
    ModelClient,
    RetryPolicy,
    SteeringInlet,
    StopPolicy,
    ToolExecutor,
)


@dataclass(frozen=True)
class TurnRequest:
    """One turn = conversation data + configuration (the claude-sdk
    "prompt + options" shape). The platform boundary: everything
    platform-specific has been translated into neutral data by here."""

    thread_id: str
    messages: list[dict[str, Any]]
    options: TurnOptions


@dataclass(frozen=True)
class LoopAssembly:
    """The loop's full dependency set (hard components + strategy seats)."""

    # hard components (turn-specific, wired by build_assembly)
    model: ModelClient
    tools: ToolExecutor
    projector: ContextProjector
    log: EventLogWriter
    cancel: CancellationSignal
    expression: ExpressionPolicy
    errors: ErrorClassifier
    compaction: CompactionPolicy
    params: ModelParams
    # strategy seats (v1 minimal implementations by default)
    stop: StopPolicy = field(default=None)  # type: ignore[assignment]
    steering: SteeringInlet = field(default=None)  # type: ignore[assignment]
    retry: RetryPolicy = field(default=None)  # type: ignore[assignment]
    hooks: Any = None
    include_arg_deltas: bool = True
    # Side-event queue: channels that produce ui events during tool
    # execution (today update_plan; tomorrow subagent announcements)
    # append here and the loop drains it at the dispatch boundary — so a
    # channel never needs a back-reference to the loop.
    side_events: list = field(default_factory=list)
    # Argument fields streamed for expression tools that carry no
    # annotations of their own (MCP tools cannot declare ours). These
    # are the conventional reply-content field names.
    reply_fields: tuple[str, ...] = ("content", "message", "text")

    def __post_init__(self) -> None:
        # Frozen dataclass defaults for the seats (kept lazy to preserve
        # the one-way import flow: contracts never import implementations).
        from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.hooks import (
            HookRegistry,
        )
        from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.steering import (
            NullSteeringInlet,
        )
        from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.stop import (
            NoMoreActionsStop,
        )
        from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
            StepRetry,
        )

        if self.stop is None:
            object.__setattr__(self, "stop", NoMoreActionsStop())
        if self.steering is None:
            object.__setattr__(self, "steering", NullSteeringInlet())
        if self.retry is None:
            # Retrying a failed STEP (rate limit, transient 5xx) is cheap:
            # the ledger already holds the history, so a retry costs one
            # step, not a turn. Distinct from turn ceilings, which the
            # framework never has (iron rule #14).
            object.__setattr__(self, "retry", StepRetry())
        if self.hooks is None:
            object.__setattr__(self, "hooks", HookRegistry.empty())


async def run_turn_events(
    request: TurnRequest,
    cancel: CancellationSignal,
    *,
    log: EventLogWriter | None = None,
) -> AsyncIterator[LoopEvent]:
    """The framework's top entry: assemble, expand, loop, stream typed
    events. Legacy-dict translation belongs to the adapter layer — new
    protocol inside, old contract at the edge."""
    from xyz_agent_context.agent_framework.llm.litellm_client import LitellmClient
    from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import ToolContext
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.expression import (
        ExpressionContract,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.loop import (
        NexusPowerLoop,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.compaction import (
        ToolResultPruner,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.model_client import (
        LiteLLMModelClient,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.profiles import (
        resolve_profile,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.projector import (
        PassthroughProjector,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.prompt_cache import (
        cache_hit_metrics,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.assembler import (
        PromptAssembler,
        PromptInputs,
        PromptMode,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.library import (
        NexusPowerPrompts,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.error_classifier import (
        DefaultErrorClassifier,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.event_log import (
        NullEventLogWriter,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.turn_ledger import (
        TurnLedger,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.builtin import (
        BuiltinToolset,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.builtin.context_tools import (
        ContextToolHandlers,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.dispatcher import (
        ToolDispatcher,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.expansion import (
        CapabilityExpander,
        Expandable,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.mcp_channel import (
        McpToolChannel,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.policy import (
        DisallowedToolsLayer,
        PolicyEngine,
        ShellConfinementLayer,
        WorkspaceConfinementLayer,
    )
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.scheduling_channel import (
        PlanState,
        SchedulingChannel,
    )

    opts = request.options
    if opts.output_schema is not None:
        raise ValueError(
            "output_schema ships in P3; the surface is declared but a v1 "
            "turn must not request it (schema honesty: fail loud)"
        )

    workspace = str(Path(opts.cwd).resolve())
    ctx = ToolContext(agent_id=opts.agent_id, workspace=workspace, extra_env=dict(opts.env))
    ledger = TurnLedger(request.thread_id)
    profile = resolve_profile(opts.model, opts.provider)
    params = ModelParams(
        model=opts.model,
        provider=opts.provider,
        api_key=opts.api_key,
        base_url=opts.base_url,
        thinking=opts.thinking,
        extra=dict(opts.llm_extra),
    )

    mcp = McpToolChannel(
        {name: McpServerSpec(url=str(spec.get("url", "")), headers=dict(spec.get("headers") or {}))
         for name, spec in opts.mcp_servers.items()}
    )
    # The expression contract is built BEFORE the expander: expansion may
    # grant delivery tools mid-turn (add_tools), and only the per-step
    # tail reminder reads the growing list — the stable prefix freezes
    # the turn-start view.
    expression = ExpressionContract(opts.expressive_tools)
    catalog = tuple(
        Expandable(
            key=e.key,
            card=e.card,
            instructions=e.instructions,
            mcp_servers={
                n: McpServerSpec(url=str(s.get("url", "")), headers=dict(s.get("headers") or {}))
                for n, s in e.mcp_servers.items()
            },
            skill_dirs=e.skill_dirs,
            extra_env=dict(e.extra_env),
            expressive_tools=e.expressive_tools,
        )
        for e in opts.expandables
    )
    expander = CapabilityExpander(
        catalog,
        add_mcp_servers=mcp.add_servers,
        add_env=ctx.extra_env.update,
        add_expressive=expression.add_tools,
    )

    dispatcher: ToolDispatcher | None = None

    def _search(query: str) -> list[str]:
        assert dispatcher is not None
        return dispatcher.search_lines(
            query, card_index=expander.card_index() if catalog else ""
        )

    def _status() -> dict[str, Any]:
        total = ledger.total_usage()
        return {
            "steps": ledger.num_steps(),
            "input_tokens_last_step": ledger.last_input_tokens(),
            "context_window": profile.context_window,
            "total": total.as_legacy_dict(),
            **cache_hit_metrics(total),
        }

    builtin = BuiltinToolset(
        ctx,
        enabled_groups=frozenset(opts.builtin_groups),
        context_handlers=ContextToolHandlers(
            expand=expander.expand if catalog else None,
            status=_status,
            search=_search,
        ),
        with_expansion=bool(catalog),
    )
    plan = PlanState()
    side_events: list[LoopEvent] = []
    scheduling = SchedulingChannel(
        plan,
        lambda steps, note: side_events.append(ledger.record_plan(steps, note)),
    )
    dispatcher = ToolDispatcher(
        (builtin, scheduling, mcp),
        policy=PolicyEngine(
            (DisallowedToolsLayer(), WorkspaceConfinementLayer(), ShellConfinementLayer())
        ),
        ctx=ctx,
        disallowed_tools=frozenset(opts.disallowed_tools),
        allowed_tools=frozenset(opts.allowed_tools),
        marker_tools=frozenset(opts.marker_tools),
    )

    try:
        await mcp.connect()
        initial_instructions = (
            await expander.expand_initial(opts.initial_expansions)
            if opts.initial_expansions
            else ""
        )
        dispatcher.invalidate()

        prompt = PromptAssembler().assemble(
            PromptInputs(
                builtin_groups=opts.builtin_groups,
                capability_cards=expander.card_index() if catalog else "",
                capability_instructions=initial_instructions,
                # Frozen at assembly: the constitution's example never
                # moves mid-turn (stable prefix). Initial expansions ran
                # above, so a reply tool granted by them counts too.
                default_reply_tool=next(iter(expression.names()), ""),
            ),
            PromptMode.FULL,
        )
        base_messages = _insert_harness(request.messages, prompt.messages())

        # The reply rule rides the DYNAMIC TAIL, not just the constitution
        # at the top: acceptance runs showed a model answering a one-word
        # question in plain text because the rule sat far from the
        # generation point. Rendered per step from the contract's CURRENT
        # list, so delivery tools granted by mid-turn expansion are named
        # too — and a message-borne reply instruction outranks the list.
        def _tail() -> str:
            reminder = NexusPowerPrompts.reply_reminder(expression.names())
            return "\n\n".join(p for p in (plan.render(), reminder) if p)

        assembly = LoopAssembly(
            model=LiteLLMModelClient(profile, LitellmClient()),
            tools=dispatcher,
            projector=PassthroughProjector(base_messages, _tail),
            log=log or NullEventLogWriter(),
            cancel=cancel,
            expression=expression,
            errors=DefaultErrorClassifier(),
            compaction=ToolResultPruner(),
            params=params,
            include_arg_deltas=opts.include_arg_deltas,
            side_events=side_events,
        )
        async for event in NexusPowerLoop(assembly, ledger).run_turn():
            yield event
    finally:
        await mcp.aclose()
        if log is not None:
            await log.flush()


def _insert_harness(
    platform_messages: list[ProviderMessage], harness_messages: list[dict[str, str]]
) -> list[ProviderMessage]:
    """Insert the harness system messages at the end of the leading
    system run (they belong with the system block, before history)."""
    cut = 0
    for message in platform_messages:
        if message.get("role") == "system":
            cut += 1
        else:
            break
    return [*platform_messages[:cut], *harness_messages, *platform_messages[cut:]]
