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

from typing import Callable

from loguru import logger

from narranexus_plugins.frameworks_nexus_power.core.contracts.protocols import ToolChannel
from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import (
    PolicyContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.tooling.policy import (
    PolicyEngine,
)

# Ceilings for every non-overview ``search_lines`` result — no query
# shape may return the whole tool surface. Tool hits and card-index
# lines are capped SEPARATELY so neither class can starve the other
# (one shared budget let 12 tool hits hide the entire capability
# index). The deliberate exception is the empty/whitespace query, which
# routes to the grouped overview: that IS the full-list request.
_SEARCH_MAX_HITS = 12
_SEARCH_MAX_CARD_HITS = 4
# Seat GUARANTEE (not top placement) inside the tool slice for
# expressive (reply) tools that pass the query filter: at most this many
# missing reply tools replace the weakest non-expressive seats, keeping
# rank order otherwise. Word-form mismatch is substring scoring's upper
# bound (a "reply" probe cannot score `speak`), and the reply surface
# vanishing from a relevant probe is the "my reply tools don't exist"
# silence spiral in ranking form. Entry bar is deliberately the plain
# filter hit (any content-token coverage) — placement no longer distorts
# order, so a looser bar costs tail seats only.
_SEARCH_MAX_EXPRESSIVE_HITS = 3
# Glue tokens that substring-match arbitrary names — they stay in FILTER
# semantics but never score: a top key decided by noise breaks the cap's
# "always drops the weakest" promise. The list only carries words the
# length gate (> 2 chars) cannot catch; 1-2 char glue (`i` hits
# bind/cli/write, `to` hits tool_search, `a`/`an`/`of`/...) is dropped
# by the gate itself.
_GLUE_TOKENS = frozenset({
    "the", "how", "for", "and", "with", "what", "can", "use",
})


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
        is_expressive: Callable[[str], bool] | None = None,
        deferred_tools: frozenset[str] = frozenset(),
    ) -> None:
        self._channels: list[ToolChannel] = list(channels)
        # Visible (searchable + callable) but absent from the up-front model
        # list: plugin tools that opted out of always_visible.
        self._deferred = deferred_tools
        self._policy = policy
        self._ctx = ctx
        self._policy_ctx = PolicyContext(tool_ctx=ctx, disallowed_tools=disallowed_tools)
        self._allowed = allowed_tools
        self._markers = marker_tools
        # Live callback (assembly passes ExpressionContract.is_expressive)
        # — NOT a frozenset snapshot: the expressive list grows mid-turn
        # via capability expansion, and a snapshot would miss delivery
        # tools granted right before the probe that needs them. Same
        # out-of-band pattern as marker_tools: MCP specs cannot carry the
        # annotation, the platform injects the fact.
        self._is_expressive = is_expressive or (lambda _n: False)
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

    def model_tools(self) -> list[ToolSpec]:
        """The tools handed to the model up front: visible minus deferred (search still sees them)."""
        return [spec for spec in self.visible_tools() if spec.name not in self._deferred]

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
                    images=result.images,
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
        """Own-algorithm tool discovery (model-agnostic): tokenized
        substring match over names and descriptions; empty query →
        grouped overview.

        Multi-word queries match per token — ALL tokens first, ANY token
        as the fallback. A single whole-string needle made a multi-word
        verification probe ("narra reply speak send") return no matches
        for tools that were in scope, and the model concluded its reply
        tools did not exist (2026-08-13 voice run).
        """
        specs = self.visible_tools()
        # Deduped, order-stable tokens: "reply to the user to ..." must
        # not double-count `to`, and dict.fromkeys keeps sort inputs
        # reproducible (a set would not).
        tokens = list(dict.fromkeys(t for t in query.lower().split() if t))
        if not tokens:
            # Empty AND whitespace-only queries are the same request: the
            # grouped overview. Routing "  " here also closes the cap
            # bypass where zero tokens made every all()/any() below
            # vacuously true (pipeline review 2026-08-13).
            lines = [f"{len(specs)} tools in scope:"]
            lines += [f"- {s.name}: {s.description.splitlines()[0][:100]}" for s in specs]
            if card_index:
                lines += ["", "Expandable capabilities:", card_index]
            return lines

        def _line(s) -> str:
            return f"- {s.name}: {s.description.splitlines()[0][:100]}"

        hays = {s.name: s.name.lower() + " " + s.description.lower() for s in specs}
        # SCORING tokens are content words only (round-4 review: glue and
        # single-letter tokens substring-match arbitrary names, so the
        # top key would be decided by noise). FILTER semantics — the ALL
        # pool and all_matched — keep the full token list. A pure-glue
        # query falls back to scoring on everything it has.
        scoring = [
            t for t in tokens if len(t) > 2 and t not in _GLUE_TOKENS
        ] or tokens

        def _score(s: ToolSpec) -> tuple[int, int, int]:
            # Tuple score, most-significant first:
            #   1. LEAF-name hit — a content token in the tool's leaf
            #      name (the `mcp__<server>__` prefix would hand one
            #      shared token to a whole module's tools);
            #   2. coverage — distinct content tokens matched (bounded
            #      by the token count, so prose length cannot inflate it);
            #   3. occurrences — tiebreak only; on the ALL path coverage
            #      ties by construction and this provides the ordering.
            hay = hays[s.name]
            leaf = s.name.rsplit("__", 1)[-1].lower()
            return (
                sum(t in leaf for t in scoring),
                sum(t in hay for t in scoring),
                sum(hay.count(t) for t in scoring),
            )

        def _ranked(pool) -> list[ToolSpec]:
            # Truncation must always drop the weakest matches; stable
            # sort keeps scope order within equal scores.
            scored = [(_score(s), s) for s in pool]
            return [
                s
                for _, s in sorted(
                    (p for p in scored if p[0][1] > 0),
                    key=lambda p: (-p[0][0], -p[0][1], -p[0][2]),
                )
            ]

        all_pool = [s for s in specs if all(t in hays[s.name] for t in tokens)]
        all_matched = bool(all_pool)
        ranked = _ranked(all_pool)
        if not ranked and len(tokens) > 1:
            # Any-token fallback: a natural-language probe whose tokens
            # include glue words must surface the strongest matches, not
            # the whole surface.
            ranked = _ranked(specs)
        # Seat guarantee for reply tools that passed the filter. The
        # expressive fact comes from the annotation OR the injected live
        # adjudicator — production reply tools are MCP specs whose
        # annotations cannot carry it (the platform declares the surface
        # per-turn via TurnOptions.expressive_tools). No filter hit -> no
        # free ride. Tools already ranked into the head keep their spots
        # (no fake top placement); missing ones replace the weakest
        # NON-expressive seats from the tail, so rank order — and the
        # "truncation drops the weakest" invariant — survive.
        def _is_expr(s: ToolSpec) -> bool:
            return s.annotations.expressive or self._is_expressive(s.name)

        head = ranked[:_SEARCH_MAX_HITS]
        seated = {s.name for s in head}
        missing = [s for s in ranked if _is_expr(s) and s.name not in seated]
        # Reversed placement: the backward victim scan hands out seats
        # from the tail inward, so iterating the strongest substitute
        # LAST parks it on the earliest (front-most) freed seat — the
        # substitutes keep their own rank order in the final slice.
        for s in reversed(missing[:_SEARCH_MAX_EXPRESSIVE_HITS]):
            for i in range(len(head) - 1, -1, -1):
                if not _is_expr(head[i]):
                    head[i] = s
                    break
            else:
                # No non-expressive seat left (head already saturated
                # with reply tools) — this substitute stays unseated.
                # Loud enough to find, quiet enough not to spam.
                logger.debug(
                    f"tool_search: no seat left for expressive "
                    f"{s.name!r}; head is all-expressive"
                )
        tool_hits = [_line(s) for s in head]
        card_hits: list[str] = []
        if card_index:
            # Mirror the mode that produced the hits: a precise ALL-token
            # query must not get loose any-token card noise appended.
            match = (
                (lambda line: all(t in line.lower() for t in tokens))
                if all_matched
                else (lambda line: any(t in line.lower() for t in tokens))
            )
            # Card lines are ranked too (display text, not ToolSpecs —
            # coverage over the line): the strongest card match claims a
            # seat ahead of glue-token noise.
            card_hits = sorted(
                (line for line in card_index.splitlines() if match(line)),
                key=lambda line: -sum(t in line.lower() for t in tokens),
            )
        # Separate ceilings: neither class can starve the other. The tool
        # side is already capped where `head` is built — single cap point.
        return tool_hits + card_hits[:_SEARCH_MAX_CARD_HITS]
