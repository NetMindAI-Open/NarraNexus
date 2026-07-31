"""
@file_name: dispatcher.py
@author: Bin Liang
@date: 2026-07-29
@description: ToolDispatcher — the single dispatcher over every
capability channel (ToolExecutor implementation).

Invariants:
  - ``visible_tools()`` = union of channel tools − disallowed
    (∩ allowlist when set), in (channel order, registration order) with
    a generation cache — expansion appends, never resorts (constraint
    C2; channels own deterministic registration);
  - every ``execute`` passes the PolicyEngine first (fail-closed); a
    deny is an error-shaped result, never an exception;
  - label tools (``marker_only`` annotation or the injected marker
    list) short-circuit after adjudication: the call IS the signal; its
    meaning lives in the event stream;
  - "what the model sees ≡ what is registered" holds by construction —
    prompts and schemas derive from the same specs.
"""

from __future__ import annotations

from loguru import logger

from xyz_agent_context.agent_framework.nexus_power.contracts.protocols import ToolChannel
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    PolicyContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.tooling.policy import (
    PolicyEngine,
)


def _missing_required(spec: ToolSpec, args: dict) -> list[str]:
    required = spec.input_schema.get("required") or ()
    return [name for name in required if name not in args]


def _compact_schema(spec: ToolSpec) -> str:
    import json

    try:
        return json.dumps(spec.input_schema, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(spec.input_schema)


class ToolDispatcher:
    """Channel registry + policy checkpoint + routing."""

    def __init__(
        self,
        channels: tuple[ToolChannel, ...],
        *,
        policy: PolicyEngine,
        ctx: ToolContext,
        disallowed_tools: frozenset[str] = frozenset(),
        allowed_tools: frozenset[str] = frozenset(),
        marker_tools: frozenset[str] = frozenset(),
    ) -> None:
        self._channels: list[ToolChannel] = list(channels)
        self._policy = policy
        self._ctx = ctx
        self._policy_ctx = PolicyContext(tool_ctx=ctx, disallowed_tools=disallowed_tools)
        self._allowed = allowed_tools
        self._markers = marker_tools
        self._cache: list[ToolSpec] | None = None
        self._cache_generations: tuple[int, ...] | None = None

    # ---- ToolExecutor ----

    def visible_tools(self) -> list[ToolSpec]:
        generations = tuple(getattr(c, "generation", 0) for c in self._channels)
        if self._cache is not None and generations == self._cache_generations:
            return list(self._cache)
        seen: set[str] = set()
        visible: list[ToolSpec] = []
        for channel in self._channels:
            # Registration order, NEVER a name sort (constraint C2): a
            # mid-turn expansion must APPEND its tools to the array —
            # sorting here would insert them into the middle and move
            # every byte after the insertion point out of the provider's
            # cached prefix. Channels own deterministic registration.
            for spec in channel.list_tools():
                if spec.name in seen:
                    logger.warning(f"duplicate tool name {spec.name!r}: first wins")
                    continue
                seen.add(spec.name)
                if spec.name in self._policy_ctx.disallowed_tools:
                    continue
                if self._allowed and spec.name not in self._allowed:
                    continue
                visible.append(spec)
        self._cache = visible
        self._cache_generations = generations
        return list(visible)

    def spec_for(self, name: str) -> ToolSpec | None:
        for spec in self.visible_tools():
            if spec.name == name:
                return spec
        return None

    async def execute(self, call: ToolCall) -> ToolResult:
        decision = self._policy.check(call, self._policy_ctx)
        if not decision.allowed:
            return ToolResult(call_id=call.id, ok=False, error=f"denied: {decision.reason}")

        spec = self.spec_for(call.name)
        if spec is None:
            return ToolResult(
                call_id=call.id, ok=False, error=f"tool {call.name!r} is not available"
            )
        if spec.annotations.marker_only or call.name in self._markers:
            # Label tool: adjudicated, then short-circuited — the event
            # stream carries the meaning; delivery is the consumer's job.
            return ToolResult(call_id=call.id, ok=True, content="delivered")

        missing = _missing_required(spec, call.args)
        if missing:
            # Pre-dispatch schema validation (hermes-shape): a call with
            # required fields absent never reaches a handler — handlers'
            # own fallbacks turned this into misleading errors (write
            # without `path` resolved to the workspace root and failed
            # as `Is a directory`). The schema comes back with the
            # error so the model can re-emit a complete call.
            return ToolResult(
                call_id=call.id,
                ok=False,
                error=(
                    f"invalid arguments for {call.name}: missing required "
                    f"{missing}. The tool was NOT executed. Expected "
                    f"schema: {_compact_schema(spec)}"
                ),
            )

        for channel in self._channels:
            if any(s.name == call.name for s in channel.list_tools()):
                result = await channel.call(call.name, call.args, self._ctx)
                return ToolResult(
                    call_id=call.id,
                    ok=result.ok,
                    content=result.content,
                    error=result.error,
                    synthetic=result.synthetic,
                )
        return ToolResult(call_id=call.id, ok=False, error=f"no channel serves {call.name!r}")

    # ---- registry / search seams ----

    def add_channel(self, channel: ToolChannel) -> None:
        """Dynamic expansion landing point (append-only)."""
        self._channels.append(channel)
        self._cache = None

    def invalidate(self) -> None:
        self._cache = None

    def search_lines(self, query: str, *, card_index: str = "") -> list[str]:
        """Own-algorithm tool discovery (model-agnostic): substring match
        over names and descriptions; empty query → grouped overview."""
        specs = self.visible_tools()
        if not query:
            lines = [f"{len(specs)} tools in scope:"]
            lines += [f"- {s.name}: {s.description.splitlines()[0][:100]}" for s in specs]
            if card_index:
                lines += ["", "Expandable capabilities:", card_index]
            return lines
        needle = query.lower()
        hits = [
            f"- {s.name}: {s.description.splitlines()[0][:100]}"
            for s in specs
            if needle in s.name.lower() or needle in s.description.lower()
        ]
        if card_index:
            hits += [
                line
                for line in card_index.splitlines()
                if needle in line.lower()
            ]
        return hits
