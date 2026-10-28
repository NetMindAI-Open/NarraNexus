"""
@file_name: profiles.py
@author: Bin Liang
@date: 2026-07-29
@description: The ProviderProfile data table — onboarding a provider is
adding a row.

Rows encode measured dialect behaviour only (cache style, window,
argument-delta support). No row ever encodes a judgement about a
model's fitness (iron rule #15): unknown providers resolve to a
conservative default so every user model runs — it merely forgoes the
optimizations until its row is measured in.

Per-model limits are NOT this table's to invent: ``resolve_profile``
overlays ``providers/model_catalog``, the platform-wide source that
``adapters/openai_agents`` and ``llm/anthropic_helper`` already read. So
every framework that goes through our own client gets the same numbers,
and adding a model is one catalog row rather than one row per caller.

The rows here carry DIALECT only, keyed by protocol. That distinction
is load-bearing: ``provider`` is the resolved protocol, and one protocol
serves many vendors — NetMind's free-tier card speaks anthropic while
serving Qwen, DeepSeek and MiMo. Keying a ceiling off it would hand a 7B
model a 128K output request.

A ceiling set below what the model allows does not save anything: it
severs tool arguments mid-JSON, and the model cannot see the cut or
recover from it (2026-07-30 incident — a single write_file retried into
a loop against an 8_192 ceiling). Cost and depth are the caller's dials
(iron rule #15), not this table's.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Mapping

from narranexus_plugins.frameworks_nexus_power.core.contracts.model import ProviderProfile
from narranexus.platform.agent_framework.providers.model_catalog import (
    get_model_meta,
    get_model_name_match,
)

_DEFAULT = ProviderProfile(name="default")

# name -> profile. Matching is by substring against provider then model
# (lowercased), first hit wins; order matters only for overlapping keys.
_PROFILES: tuple[ProviderProfile, ...] = (
    ProviderProfile(
        name="anthropic",
        cache_style="breakpoints",
        thinking_replay="strip",
        supports_arg_delta=True,
        context_window=200_000,
    ),
    ProviderProfile(
        name="deepseek",
        cache_style="prefix_auto",
        # Thinking mode: the CoT behind a tool call must come back on the
        # very next request of the same round, or the API answers 400
        # "The `reasoning_content` in the thinking mode must be passed
        # back" (measured 2026-09-08 on NetMind's OpenAI endpoint with
        # DeepSeek-V4-Pro; every multi-step tool turn died at step 2).
        # Model-keyed on purpose: the row also matches DeepSeek served
        # over a generic openai-protocol endpoint, which is where it bit.
        thinking_replay="keep",
        # NOT ``thinks_by_default=True`` here: that flag is about whether
        # the MODEL burns output budget on hidden CoT, which is a
        # per-model fact, not a per-dialect one — DeepSeek-V3 is not
        # measured to think by default the way V4-Pro/V4-Flash are (see
        # ``model_catalog.py``, which is where those two get it True).
        # Defaulting the whole "deepseek" substring match to True would
        # assume every future deepseek-family model thinks, which nobody
        # has measured.
        context_window=128_000,
    ),
    ProviderProfile(
        name="openai",
        cache_style="prefix_auto",
        thinking_replay="strip",
        supports_arg_delta=True,
        context_window=128_000,
    ),
    ProviderProfile(
        name="qwen",
        cache_style="none",
        # PROTOCOL-GUESSED wall (see ``output_wall``'s docstring on
        # ``vendor_context_window`` for why that distinction matters): no
        # catalog row for any ``Qwen/Qwen3.6-*`` id sets ``context_window``
        # (checked 2026-09-09, see model_catalog.py), so this number is
        # unverified against a real Qwen limit. Left as-is rather than
        # invented — the catalog is the only place a real number belongs.
        context_window=32_000,
    ),
)


# Room left for the request framing the estimate cannot see (tool
# schemas, system preamble, the provider's own bookkeeping).
_HEADROOM_MARGIN_TOKENS = 4_096

# Below this an "answer" cannot carry a tool call worth making, so we
# stop shrinking and let the provider reject the request honestly rather
# than silently returning something useless.
_MIN_OUTPUT_TOKENS = 1_024

# Models that think by default (``ProviderProfile.thinks_by_default``) burn
# output budget on a hidden chain-of-thought before they can reach text or
# a tool call — the CoT is not visible to us as separate accounting, it
# just eats the same ``max_tokens`` ledger. A 1_024 floor is silently
# fatal here: the model spends the whole budget thinking and the turn
# ends with ``stop_reason=max_tokens`` and zero text, zero tool calls.
# Measured against NetMind's DeepSeek-V4-Pro, the only row with in-repo
# empirical evidence (2026-09-08): max_tokens=1_024 -> 4/6 runs empty at
# ~122_880+ input tokens (the input size at which its 128_000-window
# profile's headroom clamp lands ON the 1_024 floor); max_tokens=4_096
# -> 1/6;
# max_tokens=8_192 -> 0/8.
_THINKING_MIN_OUTPUT_TOKENS = 8_192


def builtin_profiles() -> dict[str, ProviderProfile]:
    """The current table, keyed by name (read-only view for tooling)."""
    return {p.name: p for p in _PROFILES}


def output_budget(
    profile: ProviderProfile,
    input_tokens_estimate: int,
    *,
    floor_multiplier: int = 1,
) -> int:
    """The ``max_tokens`` to ask for, given what the input already costs.

    Anthropic enforces ``input + max_tokens <= context_window`` and
    answers a violation with a 400 that names neither "context window"
    nor any other marker the overflow table watches — so it would arrive
    as an unretryable INVALID_REQUEST and kill the turn.

    Measured on the dev gateway 2026-07-31: opus-4-8 accepted 144_065
    input tokens alongside ``max_tokens=128_000``, which puts its real
    window far beyond the 200_000 this module manages compaction
    against. So for the Opus and Sonnet rows this clamp never binds. It
    exists for Haiku, whose window really is 200_000: our compaction
    only trips at 150_000, leaving a band where an unclamped 64_000
    request would exceed the limit.

    The floor wins over a tight input estimate ON PURPOSE, even past the
    point where ``input + max_tokens`` would exceed the wall: a provider
    that rejects the oversized request answers with a visible 400, which
    is strictly better than silently handing a thinking-by-default model a
    budget too small to ever produce output (2026-09-08 incident). The
    floor is clamped against the model's OWN ceiling last, though — a
    thinking-by-default model whose measured ceiling sits below the
    thinking floor (DeepSeek-V3's real 7_200) must not be asked for more
    than it accepts.

    ``floor_multiplier`` scales the floor term ONLY — never the ceiling
    or the headroom term — and the ceiling still clamps last. It does NOT
    make the result respect headroom: the floor already wins over
    headroom (above), so a multiplied floor widens that same over-the-wall
    risk on a near-full context. loop.py therefore applies it only to the
    single step its output-budget-truncation retry replays.
    """
    floor = (
        _THINKING_MIN_OUTPUT_TOKENS
        if profile.thinks_by_default
        else _MIN_OUTPUT_TOKENS
    ) * floor_multiplier
    if input_tokens_estimate <= 0:
        return profile.max_output_tokens
    headroom = profile.output_wall - input_tokens_estimate - _HEADROOM_MARGIN_TOKENS
    return min(profile.max_output_tokens, max(floor, headroom))


def requested_max_tokens(
    profile: ProviderProfile,
    extra: Mapping[str, Any],
    input_tokens_estimate: int,
    *,
    floor_multiplier: int = 1,
) -> int:
    """The ``max_tokens`` value a request actually carries — the single
    source of truth for it.

    A ``max_tokens`` pinned in ``params.extra`` always wins (an explicit
    setting is never overridden, binding rule #15); otherwise it is
    ``output_budget``. "Pinned" means a value that reads as an integer:
    ``None`` (or anything ``int()`` rejects) is treated as not pinned, so
    the request carries the computed budget rather than ``max_tokens:
    null``. (The older ``extra.setdefault`` path sent an explicit ``None``
    verbatim; nothing on the platform writes one, and a null cap is not a
    budget, so the computed value is the intended behaviour.) The model client sends
    exactly this value, and loop.py's truncation retry asks this same
    function both "what did the failed step send" and "would a doubled
    floor send more" — so the two can never drift apart.
    """
    pinned = extra.get("max_tokens")
    if pinned is not None:
        try:
            return int(pinned)
        except (TypeError, ValueError):
            pass
    return output_budget(
        profile, input_tokens_estimate, floor_multiplier=floor_multiplier
    )


def resolve_profile(model: str, provider: str | None = None) -> ProviderProfile:
    """Dialect from the protocol, output limits from the model.

    The two halves have different keys and mixing them is a bug: an
    anthropic-protocol endpoint really does take cache_control
    breakpoints whatever it is serving, but how many tokens that model
    will emit is nothing to do with the protocol it speaks.
    """
    return _with_model_limits(_dialect_profile(model, provider), model)


def _dialect_profile(model: str, provider: str | None) -> ProviderProfile:
    """Match provider first, then model, by substring; default otherwise."""
    haystacks = [h.lower() for h in (provider or "", model or "") if h]
    for profile in _PROFILES:
        for haystack in haystacks:
            if profile.name in haystack:
                return profile
    # Anthropic-protocol endpoints frequently serve claude-* aliases.
    for haystack in haystacks:
        if "claude" in haystack:
            return builtin_profiles()["anthropic"]
    return _DEFAULT


def _with_model_limits(profile: ProviderProfile, model: str) -> ProviderProfile:
    """Overlay the platform's model catalog onto the dialect row.

    The catalog is the single source of truth for per-model limits and is
    already what ``adapters/openai_agents`` and ``llm/anthropic_helper``
    read — a second table here would be a second answer to the same
    question, and the two promptly disagreed (128_000 against the
    catalog's 115_200) the first time this was written locally.

    RAISING the ceiling additionally requires a measured window, because
    the two only mean anything together. The clamp sizes output against
    ``output_wall``, and an unmeasured window falls back to the dialect
    row's ``context_window`` — a number that describes the PROTOCOL, not
    this model. Pairing a model-measured ceiling with a protocol-guessed
    wall is the same defect as inventing a short wall, pointed the other
    way: GLM-5.1 would have jumped 8_192 → 117_964 under a borrowed
    200_000 wall, and it sits in the default NetMind dropdown.

    LOWERING never needs a window — a smaller ceiling cannot overrun a
    wall — so a catalog entry below the default applies unconditionally
    (DeepSeek-V3's real 7_200 is under the 8_192 default and should
    win). Unknown model → the dialect row's conservative defaults.

    A self-entered id that is not a catalog row but shares one's name
    (``get_model_name_match``) borrows only what is safe by name:
    ``thinks_by_default`` and a LOWER ceiling. Never the window, never a
    raised ceiling — a same-named custom build may have a smaller wall,
    and borrowing the catalog's would stop compaction from firing.
    """
    meta = get_model_meta(model)
    if meta is None:
        match = get_model_name_match(model)
        if match is None:
            return profile
        ceiling = profile.max_output_tokens
        if match.max_output_tokens is not None:
            ceiling = min(ceiling, match.max_output_tokens)
        return replace(
            profile,
            max_output_tokens=ceiling,
            thinks_by_default=match.thinks_by_default,
        )
    ceiling = profile.max_output_tokens
    if meta.max_output_tokens is not None:
        raising = meta.max_output_tokens > ceiling
        if not raising or meta.context_window is not None:
            ceiling = meta.max_output_tokens
    return replace(
        profile,
        max_output_tokens=ceiling,
        vendor_context_window=meta.context_window or profile.vendor_context_window,
        # Model-specific, not dialect-specific (see ``thinks_by_default``'s
        # docstring on ``ProviderProfile``) — the catalog is the only place
        # honest per-model values belong, so an unregistered model simply
        # keeps the dialect row's conservative False.
        thinks_by_default=meta.thinks_by_default,
    )
