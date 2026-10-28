"""
@file_name: assembly.py
@author: Bin Liang
@date: 2026-07-29
@description: The single wiring point — TurnRequest in, a typed event
stream out.

``LoopAssembly`` is the loop's complete dependency set ("adding a
component = adding a field with a default"); ``build_assembly`` is the
only default construction site — every wiring decision lives here and
nowhere else. Loop-level tests swap any component via
``dataclasses.replace`` (the assembly is injectable there). The
top-level ``run_turn_events`` builds its own assembly, so its caller
cannot inject one; entry tests instead patch the symbols it imports
lazily inside the function — which is why those in-function imports must
stay lazy. The whole request is JSON-serializable (the standalone-process
runner's transport precondition).

R1 decision on growth: assembly complexity is deliberately concentrated
in this one file; strategy seams default, hard components are wired
per-turn.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, AsyncIterator

from narranexus_plugins.frameworks_nexus_power.core.contracts.events import LoopEvent
from narranexus_plugins.frameworks_nexus_power.core.contracts.model import (
    McpServerSpec,
    ModelParams,
    ProviderMessage,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.options import TurnOptions
from narranexus_plugins.frameworks_nexus_power.core.contracts.protocols import (
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
    WaitRequest,
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
    # Opt-in (voice turns): a turn about to close with zero expressive
    # calls while expressive tools exist gets ONE steering nudge and one
    # more step. Off by default — group rooms and bus turns keep their
    # legal silence.
    expression_nudge: bool = False
    # Side-event queue: channels that produce ui events during tool
    # execution (today update_plan; tomorrow subagent announcements)
    # append here and the loop drains it at the dispatch boundary — so a
    # channel never needs a back-reference to the loop.
    side_events: list = field(default_factory=list)
    # Argument fields streamed for expression tools that carry no
    # annotations of their own (MCP tools cannot declare ours). These
    # are the conventional reply-content field names.
    reply_fields: tuple[str, ...] = ("content", "message", "text")
    # The `wait_for_input` control tool's handoff: the WaitChannel writes the
    # requested (clamped) seconds here, the loop reads-and-clears it at the step
    # boundary and does the blocking wait itself. Typed as the WaitRequest
    # protocol (not Any) so THIS construction site is checked — a caller passing
    # the wrong shape fails here. (The loop reads `a.wait.pending` through an
    # `Any`-typed assembly, so that read is NOT statically checked; the clamp is
    # enforced at runtime instead, at WaitRequest.request — see loop.__init__.)
    # Defaulted lazily (like the seats) so a turn without the wait tool still has
    # an inert holder.
    wait: WaitRequest = field(default=None)  # type: ignore[assignment]

    def __post_init__(self) -> None:
        # Frozen dataclass defaults for the seats (kept lazy to preserve
        # the one-way import flow: contracts never import implementations).
        from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.hooks import (
            HookRegistry,
        )
        from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.steering import (
            NullSteeringInlet,
        )
        from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.stop import (
            NoMoreActionsStop,
        )
        from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.error_classifier import (
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
        if self.wait is None:
            from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.wait_channel import (
                WaitState,
            )
            object.__setattr__(self, "wait", WaitState())


def _steer_channels(steerable: bool, wait_state: WaitRequest) -> tuple[Any, ...]:
    """The steer-only tool channels for a turn: the ``wait_for_input`` tool,
    exposed ONLY on a STEERABLE run.

    Steerability is the orchestrator's explicit decision (it registered a live
    ``SteerChannel`` for this run), carried across the serialization boundary on
    ``TurnOptions.steerable`` — deliberately NOT inferred from whether a steering
    inlet object is mounted. The subprocess runner (``runner.main``, the default
    cloud/local path) mounts a ``QueueSteeringInlet`` on EVERY turn, fed only on a
    steerable one, so "an inlet is present" is always true and gates nothing;
    keying on it would leave ``wait_for_input`` on every prompt. On a
    non-steerable run nothing can ever feed the inlet, so the tool could only
    block up to its clamp (default 60s, max 300s) on a queue with no producer —
    which is why it must not appear. DRAIN is orthogonal: an empty inlet drains
    to nothing whether or not the run is steerable, so mid-turn injection is
    unaffected. Pure + tiny so the one wiring decision is unit-tested through the
    PRODUCTION arm (a steerable-flag mistake would otherwise only show as a real
    run blocking a full minute); deliberately NOT a registry — one conditional
    channel is not a plugin mechanism (over-design)."""
    if not steerable:
        return ()
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.wait_channel import (
        WaitChannel,
    )
    return (WaitChannel(wait_state),)


def mcp_spec_from_config(cfg: dict[str, Any]) -> McpServerSpec:
    """The turn's ``mcp_servers`` entry → channel spec.

    Both shapes the platform emits: ``{url, headers?}`` for module / URL
    servers and ``{command, args, env}`` for a plugin's stdio server. The
    second was silently read as an empty URL before, so every template stdio
    server "connected" to nothing and its tools never existed for the agent.
    """
    if cfg.get("command"):
        return McpServerSpec(
            command=str(cfg["command"]),
            args=tuple(str(a) for a in cfg.get("args") or ()),
            env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
        )
    return McpServerSpec(url=str(cfg.get("url", "")), headers=dict(cfg.get("headers") or {}))


async def run_turn_events(
    request: TurnRequest,
    cancel: CancellationSignal,
    *,
    log: EventLogWriter | None = None,
    steering: SteeringInlet | None = None,
) -> AsyncIterator[LoopEvent]:
    """The framework's top entry: assemble, expand, loop, stream typed
    events. Legacy-dict translation belongs to the adapter layer — new
    protocol inside, old contract at the edge.

    ``steering`` is the live-injection inlet the loop drains at each step
    boundary; ``None`` mounts ``NullSteeringInlet`` (the default — every
    turn today). A transport layer supplies a fed inlet (e.g. a
    ``QueueSteeringInlet`` the runner writes room/chat messages into);
    this entry only threads it to the loop."""
    from narranexus.platform.agent_framework.llm.litellm_client import LitellmClient
    from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import ToolContext
    from narranexus_plugins.frameworks_nexus_power.core import extension_points as ep
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.loop import (
        NexusPowerLoop,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.model_client import (
        LiteLLMModelClient,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.profiles import (
        resolve_profile,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.prompt_cache import (
        cache_hit_metrics,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.prompts.assembler import (
        PromptAssembler,
        PromptInputs,
        PromptMode,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.prompts.library import (
        NexusPowerPrompts,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.error_classifier import (
        DefaultErrorClassifier,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.event_log import (
        NullEventLogWriter,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.turn_ledger import (
        TurnLedger,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.builtin import (
        BuiltinToolset,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.builtin.context_tools import (
        ContextToolHandlers,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.dispatcher import (
        ToolDispatcher,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.expansion import (
        CapabilityExpander,
        Expandable,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.mcp_channel import (
        McpToolChannel,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
        PolicyEngine,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.wait_channel import (
        WaitState,
    )
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.scheduling_channel import (
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
    ctx = ToolContext(
        agent_id=opts.agent_id,
        workspace=workspace,
        extra_env=dict(opts.env),
        extra_accessible_roots=tuple(opts.extra_accessible_roots),
    )
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

    mcp = McpToolChannel({name: mcp_spec_from_config(spec) for name, spec in opts.mcp_servers.items()})
    # The expression contract is built BEFORE the expander: expansion may
    # grant delivery tools mid-turn (add_tools), and only the per-step
    # tail reminder reads the growing list — the stable prefix freezes
    # the turn-start view.
    # Strategy seats come from the framework's extension points (default
    # providers = the classes this file used to construct directly; another
    # plugin or an NX_BIND__ binding may replace them).
    seat = ep.SeatContext(options=opts, workspace=workspace, tool_context=ctx, profile=profile)
    expression = ep.resolve_one(ep.EXPRESSION, seat)
    catalog = tuple(
        Expandable(
            key=e.key,
            card=e.card,
            instructions=e.instructions,
            mcp_servers={n: mcp_spec_from_config(s) for n, s in e.mcp_servers.items()},
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
    # The WaitState is shared with the loop (LoopAssembly.wait) so the tool's
    # request reaches the boundary; the tool itself is exposed only on a
    # steerable turn (see _steer_channels).
    wait_state = WaitState()
    dispatcher = ToolDispatcher(
        (builtin, scheduling, *_steer_channels(opts.steerable, wait_state), mcp),
        # The three builtin safety layers are NOT negotiable through the seat:
        # a binding that names only a custom layer used to REPLACE workspace
        # and shell confinement (the 2026-08-07 executor-escape mitigations)
        # for every turn on the host, silently. Mandatory layers run first,
        # the seat's providers append; a layer bound twice is deduplicated by
        # type.
        policy=PolicyEngine(_with_mandatory_layers(ep.resolve_many(ep.POLICY, seat))),
        ctx=ctx,
        disallowed_tools=frozenset(opts.disallowed_tools),
        deferred_tools=frozenset(opts.deferred_tools),
        allowed_tools=frozenset(opts.allowed_tools),
        marker_tools=frozenset(opts.marker_tools),
        # Live adjudicator, not a snapshot: the expressive list grows
        # mid-turn (capability expansion), and tool_search's reserved
        # reply seats must see tools granted moments earlier.
        is_expressive=expression.is_expressive,
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
            PromptMode(opts.prompt_mode),
        )
        base_messages = _insert_harness(request.messages, prompt.messages())

        # The reply rule rides the DYNAMIC TAIL, not just the constitution
        # at the top: acceptance runs showed a model answering a one-word
        # question in plain text because the rule sat far from the
        # generation point. Rendered per step from the contract's CURRENT
        # list, so delivery tools granted by mid-turn expansion are named
        # too — and a message-borne reply instruction outranks the list.
        def _tail() -> str:
            # The origin line leads the reminder: "where am I / who am I
            # talking to" before "and here is how you answer them". Both are
            # dynamic-tail because both are per-turn facts; the origin line is
            # constant within a turn but travels with the rule it qualifies,
            # so the two can never be read apart.
            reminder = NexusPowerPrompts.reply_reminder(expression.names())
            parts = (plan.render(), opts.origin_declaration, reminder)
            return "\n\n".join(p for p in parts if p)

        projector_seat = dataclasses.replace(seat, base_messages=base_messages, tail=_tail)
        assembly = LoopAssembly(
            model=LiteLLMModelClient(profile, LitellmClient()),
            tools=dispatcher,
            projector=ep.resolve_one(ep.PROJECTOR, projector_seat),
            log=log or NullEventLogWriter(),
            cancel=cancel,
            expression=expression,
            errors=DefaultErrorClassifier(),
            compaction=ep.resolve_one(ep.COMPACTION, seat),
            stop=ep.resolve_one(ep.STOP, seat),
            params=params,
            include_arg_deltas=opts.include_arg_deltas,
            expression_nudge=opts.expression_nudge,
            side_events=side_events,
            steering=steering,  # None → LoopAssembly mounts NullSteeringInlet
            wait=wait_state,  # shared with the WaitChannel above
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


def _with_mandatory_layers(bound: tuple) -> tuple:
    """Mandatory safety layers first, then the seat's layers (types already present are not repeated)."""
    from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
        DisallowedToolsLayer,
        ShellConfinementLayer,
        WorkspaceConfinementLayer,
    )

    mandatory = (DisallowedToolsLayer(), WorkspaceConfinementLayer(), ShellConfinementLayer())
    present = {type(layer) for layer in mandatory}
    return mandatory + tuple(layer for layer in bound if type(layer) not in present)
