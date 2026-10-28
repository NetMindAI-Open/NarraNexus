"""
@file_name: model_catalog.py
@author: Bin Liang
@date: 2026-03-23
@description: Static model catalog — default model lists and metadata lookup

Provides:
- Default model lists for auto-populating providers (NetMind, Claude OAuth, etc.)
- Metadata lookup for known models (max output tokens, etc.)

The catalog is NOT indexed by preset/source. Instead:
- get_default_models(source, protocol) returns default model IDs for pre-population

Usage:
    from narranexus.platform.agent_framework.providers.model_catalog import (
        get_default_models,
        get_max_output_tokens,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from narranexus.platform.schema.provider_schema import (
    SUBSCRIPTION_AUTH_TYPES,
    ProviderProtocol,
    SlotName,
)


# =============================================================================
# Model Metadata
# =============================================================================

@dataclass(frozen=True)
class ModelMeta:
    """Known metadata for a model (output limits, etc.)"""
    model_id: str
    display_name: str
    max_output_tokens: Optional[int] = None   # 90% of model limit
    # The provider's hard `input + max_tokens` limit — the number a
    # request 400s at, not a budget we choose. Only meaningful for
    # callers that size output against the room the input leaves;
    # `None` means unverified, and they fall back to their own budget.
    context_window: Optional[int] = None
    # Does this model spend output-token budget on a hidden chain-of-
    # thought before it can reach text or a tool call, WITHOUT the caller
    # asking for it? Read by nexus_power's ``output_budget`` to raise its
    # output floor for exactly the models that need it (2026-09-09).
    # ONLY set True where there is a defensible source — an in-repo
    # measurement, or well-established, undisputed public documentation
    # for the model family. Left False (the honest "not measured" default,
    # not "confirmed non-thinking") for every row this change did not
    # independently verify — see the per-row comments below.
    thinks_by_default: bool = False


# =============================================================================
# Known Model Metadata Registry
# =============================================================================

_KNOWN_MODELS: dict[str, ModelMeta] = {}
# Lower-cased LAST path segment of every registered id -> the entries sharing
# it. Lets ``get_model_name_match`` recognise a self-entered spelling of a
# catalog model ("deepseek-v4-pro", "netmind/deepseek-ai/DeepSeek-V4-Pro").
_KNOWN_MODELS_BY_NAME: dict[str, list[ModelMeta]] = {}


def _model_name_key(model_id: str) -> str:
    return model_id.rsplit("/", 1)[-1].lower()


def _register(*models: ModelMeta) -> None:
    for m in models:
        _KNOWN_MODELS[m.model_id] = m
        _KNOWN_MODELS_BY_NAME.setdefault(_model_name_key(m.model_id), []).append(m)


# --- NetMind models ---
# `max_output_tokens` left None for newer entries whose official limits
# we have not yet verified — callers fall back to the provider's own cap.
#
# `thinks_by_default` sourcing (added for B-03, the output-budget-floor fix):
# - DeepSeek-V4-Pro / V4-Flash (and V4.1-Flash, mirrored): True, measured IN-REPO against NetMind's
#   OpenAI-protocol endpoint (2026-09-08 incident — see profiles.py's
#   ``_THINKING_MIN_OUTPUT_TOKENS`` comment for the empty-run counts at
#   each max_tokens value tried). The strongest evidence tier we have.
# - DeepSeek-V3: left False. This is the older, non-"V4" DeepSeek chat
#   line and was not part of the 2026-09-08 measurement; marking it True
#   by family-name association would be exactly the invented-fact the
#   catalog ceilings above are already disciplined against — not extending
#   that discipline to a boolean would be inconsistent.
# - Gemini 3.1 (pro / flash-lite), Kimi K2.5 / K2.6, GLM-5 / GLM-5.1,
#   MiniMax M2.7, Qwen3.6 (Plus / Flash / 35B-A3B): left False. No
#   in-repo measurement exists for any of them against NetMind's actual
#   default request shape, and general public documentation about each
#   vendor's specific default (thinking on/off, and whether NetMind's own
#   deployment overrides that default) is not something this change can
#   verify without live access. Honest "not measured" rather than a
#   guessed True or a guessed False-with-false-confidence.
_register(
    ModelMeta("minimax/minimax-m2.7", "MiniMax M2.7", max_output_tokens=58982),
    ModelMeta("google/gemini-3.1-pro-preview", "Gemini 3.1 Pro", max_output_tokens=58982),
    ModelMeta("google/gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite", max_output_tokens=58982),
    ModelMeta("moonshotai/Kimi-K2.5", "Kimi K2.5", max_output_tokens=58981),
    ModelMeta("moonshotai/Kimi-K2.6", "Kimi K2.6"),
    ModelMeta("zai-org/GLM-5", "GLM-5", max_output_tokens=117964),
    ModelMeta("zai-org/GLM-5.1", "GLM-5.1", max_output_tokens=117964),
    ModelMeta("deepseek-ai/DeepSeek-V3", "DeepSeek V3", max_output_tokens=7200),
    ModelMeta("deepseek-ai/DeepSeek-V4-Pro", "DeepSeek V4 Pro", thinks_by_default=True),
    ModelMeta("deepseek-ai/DeepSeek-V4-Flash", "DeepSeek V4 Flash", thinks_by_default=True),
    # V4.1-Flash replaced V4-Flash as the NetMind onboarding default (V4-Flash
    # left NetMind's public catalogue ~2026-09-11). Flag mirrored from the
    # V4 family; not separately measured in-repo — True is the conservative
    # direction (it only raises the output-budget floor).
    ModelMeta("deepseek-ai/DeepSeek-V4.1-Flash", "DeepSeek V4.1 Flash", thinks_by_default=True),
    # Anthropic Claude routed via NetMind's anthropic-protocol endpoint.
    # NetMind prefixes the upstream model id with "anthropic/", which is
    # how its inference router dispatches; the prefix is part of the
    # model id, not a separate provider. max_output_tokens matches the
    # native Claude limits because NetMind is a transparent proxy here.
    ModelMeta("anthropic/claude-opus-4-8", "Claude Opus 4.8 (NetMind)",
              max_output_tokens=115200, context_window=1_000_000),
    ModelMeta("anthropic/claude-sonnet-4-6", "Claude Sonnet 4.6 (NetMind)",
              max_output_tokens=115200, context_window=1_000_000),
    ModelMeta("Qwen/Qwen3.6-Plus", "Qwen3.6 Plus"),
    ModelMeta("Qwen/Qwen3.6-Flash", "Qwen3.6 Flash"),
    ModelMeta("Qwen/Qwen3.6-35B-A3B", "Qwen3.6 35B-A3B"),
)

# --- Anthropic / Claude models ---
# max_output_tokens left None for models whose official limits we haven't
# independently verified; callers fall back to the provider's own cap.
_register(
    ModelMeta("claude-opus-4-8", "Claude Opus 4.8",
              max_output_tokens=115200, context_window=1_000_000),
    ModelMeta("claude-sonnet-4-6", "Claude Sonnet 4.6",
              max_output_tokens=115200, context_window=1_000_000),
    ModelMeta("claude-haiku-4-5", "Claude Haiku 4.5",
              max_output_tokens=57600, context_window=200_000),
    ModelMeta("claude-haiku-4-5-20251001", "Claude Haiku 4.5 (2025-10-01)",
              max_output_tokens=57600, context_window=200_000),
    # Claude Code CLI family aliases — always resolve to the latest model of
    # each family. Used by the Claude OAuth candidate list so it never goes
    # stale; only valid on the CLI (`claude --model opus`), not the raw API.
    ModelMeta("opus", "Claude Opus (latest)"),
    ModelMeta("sonnet", "Claude Sonnet (latest)"),
    ModelMeta("haiku", "Claude Haiku (latest)"),
)

# What each CLI family alias means on a raw Anthropic-compatible API, where
# aliases are rejected with 400 (upstream #57 → surfaced as no_reply). Model
# strings are free text end to end — a user can type "opus" into an api_key
# provider — so transport-level normalization is the safety net, not UI
# validation. Kept adjacent to the alias registrations above: when a family
# ships a new latest, update both together
# (test_alias_targets_are_registered_catalog_models guards against typos).
_CLI_ALIAS_TO_MODEL_ID: dict[str, str] = {
    "opus": "claude-opus-4-8",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5",
}


def resolve_cli_alias(model_id: str, *, auth_type: str) -> str:
    """Return the model id a given transport should actually be sent.

    The OAuth/CLI paths (host-CLI ``oauth`` and setup-token ``oauth_token``)
    resolve family aliases themselves ("latest of family" must not go stale
    in our code) — keep them verbatim there. Every other transport (api_key,
    bearer_token proxies) speaks the raw Messages API, which rejects
    aliases, so map them to the family's current full id. Full ids and
    unknown strings pass through untouched.
    """
    if auth_type in SUBSCRIPTION_AUTH_TYPES:
        return model_id
    return _CLI_ALIAS_TO_MODEL_ID.get(model_id, model_id)


def is_cli_family_alias(model_id: str) -> bool:
    """True when ``model_id`` is a CLI family alias ("opus"/"sonnet"/"haiku").

    Needed by ``ClaudeConfig.to_cli_env``: pointing the CLI's
    ``ANTHROPIC_DEFAULT_*_MODEL`` redirect env vars at an ALIAS is
    self-referential and makes the CLI reject the model outright
    ("There's an issue with the selected model") — those redirects may
    only carry concrete model ids.
    """
    return model_id in _CLI_ALIAS_TO_MODEL_ID


def claude_family_alias(model_id: str) -> Optional[str]:
    """Map a pinned Claude full id to its CLI family alias, or None.

    ``claude-sonnet-4-6`` → ``sonnet``; already-an-alias passes through;
    anything without a recognizable ``claude-{family}-`` shape (including
    aggregator-prefixed ids like ``anthropic/claude-...``, which belong to
    API-proxy cards where full ids are correct) returns None.
    """
    if model_id in _CLI_ALIAS_TO_MODEL_ID:
        return model_id
    for family in _CLI_ALIAS_TO_MODEL_ID:
        if model_id.startswith(f"claude-{family}-"):
            return family
    return None


def effective_card_models(source: str, stored: Optional[list]) -> list[str]:
    """The model list a provider card presents at runtime.

    Single source of truth shared by EVERY card-load path. The OAuth CLI
    cards' lists are not user data: the claude CLI resolves family aliases
    to the newest of each family, and the codex curated list tracks the
    codex CLI's picker — so both are overridden from the catalog on every
    read and can never go stale. Everything else keeps the stored column.

    History: ``get_user_config`` applied this override inline while the
    resolver path built cards via ``ProviderCard.from_row`` — raw column,
    no override — so slot self-heal judged membership against the raw
    list, a slot pinned to a legacy full id ("claude-sonnet-4-6") counted
    as healthy, and the CLI stayed pinned to a stale model forever ("local
    CC model name never updates", Base recvqEiNbacKWa).
    """
    if source == "claude_oauth":
        return get_default_models("claude_oauth", "anthropic")
    if source == "codex_oauth":
        return get_default_models("codex_oauth", "openai")
    return [str(m) for m in (stored or [])]


# --- OpenAI models ---
# Text / chat / reasoning models surfaced as in-UI suggestions.
#
# `thinks_by_default`: o3 / o4-mini are True — OpenAI's o-series has no
# non-reasoning mode at all (there is no request-time toggle to turn
# reasoning off), which is well-established, undisputed public behaviour
# for the family, not a per-deployment guess. The gpt-5.x line is left
# False: GPT-5 exposes a configurable reasoning-effort control (including
# a low/"minimal" setting), so "does it default to spending a thinking-
# sized output budget" is a quantitative question this change cannot
# verify without a live measurement — the same "don't invent a number"
# discipline as the catalog ceilings above, applied to this boolean.
_register(
    ModelMeta("gpt-5.5", "GPT-5.5"),
    ModelMeta("gpt-5.4", "GPT-5.4"),
    ModelMeta("gpt-5.4-mini", "GPT-5.4 Mini"),
    ModelMeta("gpt-5.4-nano", "GPT-5.4 Nano"),
    ModelMeta("gpt-5.2", "GPT-5.2"),
    ModelMeta("gpt-5.2-mini", "GPT-5.2 Mini"),
    ModelMeta("gpt-5.1", "GPT-5.1"),
    ModelMeta("gpt-5", "GPT-5"),
    ModelMeta("gpt-4.1", "GPT-4.1"),
    ModelMeta("o4-mini", "o4-mini (reasoning)", thinks_by_default=True),
    ModelMeta("o3", "o3 (reasoning)", thinks_by_default=True),
)


# =============================================================================
# Default Model Lists (for pre-populating providers)
# =============================================================================

# Key: (source, protocol) → list of default model IDs
_DEFAULT_MODELS: dict[tuple[str, str], list[str]] = {
    # NetMind Anthropic protocol → agent models.
    # claude-opus-4-8 and claude-sonnet-4-6 sit at the top: when a new
    # user adds a NetMind provider we want Claude available out of the
    # box, since the free-tier agent model defaults to Sonnet 4.6.
    ("netmind", "anthropic"): [
        "anthropic/claude-opus-4-8",
        "anthropic/claude-sonnet-4-6",
        "minimax/minimax-m2.7",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.6-Flash",
        "zai-org/GLM-5.1",
    ],
    # NetMind OpenAI protocol → helper_llm models
    ("netmind", "openai"): [
        "minimax/minimax-m2.7",
        "google/gemini-3.1-pro-preview",
        "google/gemini-3.1-flash-lite-preview",
        "moonshotai/Kimi-K2.5",
        "moonshotai/Kimi-K2.6",
        "zai-org/GLM-5",
        "zai-org/GLM-5.1",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V4.1-Flash",
        "Qwen/Qwen3.6-Plus",
        "Qwen/Qwen3.6-Flash",
        "Qwen/Qwen3.6-35B-A3B",
        "BAAI/bge-m3",
        "nvidia/NV-Embed-v2",
        "dunzhang/stella_en_1.5B_v5",
    ],
    # Yunwu Anthropic protocol → Claude models (Yunwu proxies official Claude)
    ("yunwu", "anthropic"): [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    # Yunwu OpenAI protocol → OpenAI models (Yunwu proxies official OpenAI)
    ("yunwu", "openai"): [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "gpt-5.1",
    ],
    # OpenRouter Anthropic protocol → Claude models (OpenRouter proxies official Claude)
    ("openrouter", "anthropic"): [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
    ],
    # OpenRouter OpenAI protocol → OpenAI models (OpenRouter proxies official OpenAI)
    ("openrouter", "openai"): [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.2",
        "gpt-5.1",
    ],
    # Claude OAuth → agent models. Use the Claude Code CLI's family ALIASES
    # (`claude --model opus|sonnet|haiku` resolves to the latest of each family)
    # instead of pinned versions — so the OAuth candidate list auto-tracks the
    # newest Claude release and never needs a manual version bump on every
    # Opus/Sonnet/Haiku update. (Only the OAuth path goes through the CLI, where
    # these aliases are valid; the API-proxy providers above need full ids.)
    ("claude_oauth", "anthropic"): [
        "opus",
        "sonnet",
        "haiku",
    ],
    # Codex CLI OAuth cards. SINGLE SOURCE for the curated codex list:
    # user_service.CODEX_CURATED_MODELS reads THIS entry (it used to own the
    # constant, which left get_default_models("codex_oauth", ...) empty — so
    # codex verify_live's "curated defaults first" guard silently fell back to
    # the stale stored models column it was built to distrust). The ids are
    # full model names (the codex CLI has no family aliases); update them from
    # the codex CLI's interactive model picker — the pin test
    # test_codex_curated_models_stay_registered_in_catalog keeps them
    # consistent with the catalog registry.
    ("codex_oauth", "openai"): [
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
    ],
}

# Suggested models when user adds a generic Anthropic / OpenAI provider.
#
# These are the "official channel" pre-populated lists. They feed:
#   - /api/providers/catalog → frontend Section 2 assignment dropdowns
#     (when a custom provider points at api.openai.com / api.anthropic.com)
#   - get_official_models() for the same purpose on the server side
#
# The richer per-vendor chip suggestions (Gemini, GLM, Kimi, Qwen, MiniMax,
# DeepSeek, …) live in the frontend as MODEL_SUGGESTION_GROUPS — those are
# UI affordances for the create form, not authoritative capability data,
# and every vendor we include there is accessed via OpenAI-compatible proxy,
# so they all fall under the "openai" protocol too.
_SUGGESTED_MODELS: dict[str, list[str]] = {
    "anthropic": [
        "claude-opus-4-8",
        "claude-sonnet-4-6",
        "claude-haiku-4-5",
        "claude-haiku-4-5-20251001",
    ],
    "openai": [
        # Top recent text / chat / reasoning models. ``gpt-5.5`` is
        # the current flagship and the codex CLI default — keep it
        # first so a user adding a generic OpenAI provider for the
        # codex_cli agent framework gets it in the dropdown by
        # default.
        "gpt-5.5",
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.4-nano",
        "gpt-5.2",
        "gpt-5.2-mini",
        "gpt-5.1",
        "gpt-5",
        "gpt-4.1",
        "o4-mini",
        "o3",
    ],
}


# =============================================================================
# Query Functions
# =============================================================================

def get_default_models(source: str, protocol: str) -> list[str]:
    """
    Get default model IDs for a provider source + protocol combination.

    Used to pre-populate the models list when a provider is created.
    For user-created providers, returns suggested models based on protocol.

    Args:
        source: Provider source ("netmind", "claude_oauth", "user")
        protocol: Provider protocol ("anthropic", "openai")

    Returns:
        List of model ID strings
    """
    # Auto-discovered sources: the probe ledger ([[model_probe_ledger]]) is
    # authoritative once populated (overwrite semantics — only models that
    # actually answer on this protocol are listed). Fall back to the hardcoded
    # list when the ledger has no entry yet (fresh checkout before first sync,
    # or a source we haven't probed).
    from narranexus.platform.agent_framework.providers.model_probe_ledger import ledger_models

    # The free-tier card is gated by OUR gateway: the daily pass writes the
    # gateway-∩-verdicts list as the ledger's own netmind_free entry
    # (model_sync.build_free_tier_entry). Prefer it — the raw netmind passes
    # include models the gateway cannot route or price.
    if source == "netmind_free":
        gated = ledger_models("netmind_free", protocol)
        if gated:
            return gated

    # Otherwise the free-tier card proxies the SAME upstream catalogue as a
    # NetMind card (our gateway forwards to NetMind), so it reads netmind's
    # ledger entry instead of maintaining a second list that would drift.
    ledger_source = "netmind" if source == "netmind_free" else source
    if ledger_source in ("netmind", "system_pool", "openrouter", "yunwu"):
        synced = ledger_models(ledger_source, protocol)
        if synced:
            return synced

    # Check exact (source, protocol) match first. `ledger_source` is used as the
    # lookup key too, so the free-tier card inherits netmind's hardcoded list on
    # a fresh checkout (before the first probe sync populates the ledger) rather
    # than starting with an empty dropdown.
    defaults = _DEFAULT_MODELS.get((ledger_source, protocol))
    if defaults is not None:
        return list(defaults)

    # For user-created providers, return suggestions based on protocol
    if source == "user":
        return list(_SUGGESTED_MODELS.get(protocol, []))

    return []


def get_model_meta(model_id: str) -> Optional[ModelMeta]:
    """
    Look up a model's metadata, tolerating a routing prefix.

    Platform ids carry the upstream router's prefix ("anthropic/" on
    NetMind), so an exact miss retries on the bare id. Resolving the
    WHOLE meta in one call — rather than looking each field up
    separately — is what keeps a model's numbers coming from a single
    entry: two independent lookups can each fall back differently and
    pair one row's ceiling with another row's window.

    This is an IDENTITY lookup: it answers only for an id that names a
    catalog row. A self-entered spelling that merely shares a row's name
    is not that row — see ``get_model_name_match`` for the narrow facts
    such a spelling may borrow.

    The normalization lives here rather than in a caller so every
    consumer inherits it; a copy per caller is the duplication this
    catalog exists to prevent.
    """
    if not model_id:
        return None
    meta = _KNOWN_MODELS.get(model_id)
    if meta is None and "/" in model_id:
        meta = _KNOWN_MODELS.get(model_id.split("/", 1)[1])
    return meta


@dataclass(frozen=True)
class ModelNameMatch:
    """The facts a self-entered model id may borrow from catalog rows that
    share its name — deliberately NOT a ``ModelMeta``.

    Sharing a name does not make it the same model: a BYOK user's
    ``myorg/DeepSeek-V4-Pro`` may be a quantised or fine-tuned build with
    a smaller window. So the match carries no ``model_id`` /
    ``display_name`` (they would name another row) and no
    ``context_window`` (a borrowed wall would size compaction and output
    against a limit this model may not have). What it does carry is safe
    to apply by name: ``thinks_by_default`` (a family trait), and
    ``max_output_tokens`` which a consumer may only use to LOWER its own
    ceiling — a smaller ceiling can never overrun any wall.
    """
    thinks_by_default: bool
    max_output_tokens: Optional[int]


def get_model_name_match(model_id: str) -> Optional[ModelNameMatch]:
    """Match an id that ``get_model_meta`` does not know, case-insensitively
    on its LAST path segment ("deepseek-v4-pro", "DeepSeek-V4-Pro" and
    "netmind/deepseek-ai/DeepSeek-V4-Pro" all share the catalog's
    "deepseek-ai/DeepSeek-V4-Pro" name).

    Answers only when every catalog entry sharing the name agrees on the
    facts it returns; a name whose entries disagree is ambiguous and stays
    unknown rather than borrowing one arbitrary row's numbers. No fuzzy
    matching: a near-miss name is unknown.
    """
    if not model_id:
        return None
    candidates = _KNOWN_MODELS_BY_NAME.get(_model_name_key(model_id), [])
    facts = {(m.thinks_by_default, m.max_output_tokens) for m in candidates}
    if len(facts) != 1:
        return None
    thinks_by_default, max_output_tokens = facts.pop()
    return ModelNameMatch(
        thinks_by_default=thinks_by_default, max_output_tokens=max_output_tokens
    )


def get_max_output_tokens(model_id: str) -> Optional[int]:
    """
    Look up the max output tokens for a given model ID.

    Returns None if the model is not found.
    """
    meta = get_model_meta(model_id)
    return meta.max_output_tokens if meta else None


def get_context_window(model_id: str) -> Optional[int]:
    """
    Look up the hard `input + max_tokens` limit for a given model ID.

    Returns None if unverified — callers must fall back to a budget of
    their own rather than inventing a wall, since a wall of our own
    invention is wrong in both directions: too short silently shrinks
    every request near it, too long stops the clamp protecting anything.
    """
    meta = get_model_meta(model_id)
    return meta.context_window if meta else None


def get_model_display_name(model_id: str) -> str:
    """
    Get a human-readable display name for a model.

    Falls back to the model_id itself if not in the catalog.
    """
    meta = _KNOWN_MODELS.get(model_id)
    return meta.display_name if meta else model_id


def get_all_known_models() -> dict[str, dict]:
    """
    Get all known model metadata for API/frontend use.

    Returns:
        Dict mapping model_id to metadata dict
    """
    return {
        model_id: {
            "model_id": m.model_id,
            "display_name": m.display_name,
            "max_output_tokens": m.max_output_tokens,
            "context_window": m.context_window,
        }
        for model_id, m in _KNOWN_MODELS.items()
    }


def get_suggested_models(protocol: str) -> list[str]:
    """
    Get suggested model IDs for a given protocol.

    Used by the frontend to show suggestions when the user adds
    a new Anthropic/OpenAI protocol provider.
    """
    return list(_SUGGESTED_MODELS.get(protocol, []))


# =============================================================================
# One-key onboarding defaults
# =============================================================================

# Defaults the /api/providers/onboard endpoint wires when a user pastes a
# single key. BYOK rationale: the user pays with their own key, so the
# agent slot of the OFFICIAL families (anthropic / openai / yunwu /
# openrouter) defaults to the strongest model of that family;
# cost-sensitive users downgrade in Settings → Providers (Advanced).
# The NetMind.AI Power sources (netmind / netmind_free) are the exception
# by Owner decision: agent AND helper both start on the cost-effective
# DeepSeek-V4.1-Flash and the user trades cost for capability by switching.
# The cloud free tier additionally pays out of a fixed wallet (see
# providers/free_tier.py). The same table is the slot self-heal's preferred
# repair target (see get_onboard_default_model).
_ONBOARD_AGENT_MODELS: dict[str, str] = {
    "anthropic": "claude-opus-4-8",
    "openai": "gpt-5.5",          # codex_cli's current flagship default
    # NetMind one-key card: DeepSeek-V4.1-Flash is the default on the
    # aggregator (Owner decision, same as the helper slot); V4-Pro and
    # Claude-via-NetMind stay available in the dropdown.
    "netmind": "deepseek-ai/DeepSeek-V4.1-Flash",
    "netmind_free": "deepseek-ai/DeepSeek-V4.1-Flash",
    # Yunwu / OpenRouter proxy the official APIs — same defaults as a
    # direct Claude key.
    "yunwu": "claude-opus-4-8",
    "openrouter": "claude-opus-4-8",
}

# Helper does small structured jobs (entity extraction, narrative
# updates, fallback replies) — cheap + fast wins.
_ONBOARD_HELPER_MODELS: dict[str, str] = {
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-5.4-mini",
    "netmind": "deepseek-ai/DeepSeek-V4.1-Flash",
    "netmind_free": "deepseek-ai/DeepSeek-V4.1-Flash",
    "yunwu": "gpt-5.4-mini",
    "openrouter": "gpt-5.4-mini",
}


def get_default_agent_model(provider_type: str) -> str:
    """Default agent-slot model for one-key onboarding, per provider type."""
    return get_onboard_default_model(provider_type, SlotName.AGENT.value) or (
        _ONBOARD_AGENT_MODELS["anthropic"]
    )


def get_default_helper_model(provider_type: str) -> str:
    """Default helper_llm-slot model for one-key onboarding, per provider type."""
    return get_onboard_default_model(provider_type, SlotName.HELPER_LLM.value) or (
        _ONBOARD_HELPER_MODELS["openai"]
    )


def get_onboard_default_model(source: str, slot_name: Optional[str]) -> Optional[str]:
    """Onboarding default for ``source`` (a provider type / card source) + slot kind, or ``None``.

    THE single server-side source of truth for "which model does the platform
    put a card on": one-key onboarding, slot self-heal and the ``/catalog``
    endpoint (``get_onboard_defaults``) all read it. The free-tier card is the
    one source whose default is an ops decision, so it goes through
    ``free_tier.free_tier_default_models()`` (env ``FREE_TIER_AGENT_MODEL`` /
    ``FREE_TIER_HELPER_MODEL``, empty env -> constant).

    Unlike ``get_default_*_model`` this does NOT fall back to the anthropic /
    openai entry for unknown keys: slot self-heal uses it to decide whether the
    platform has an opinion about this card at all (a plain ``user`` BYOK card
    has none, so its repair keeps the card-order behaviour). Only the agent
    and helper_llm slots have onboarding defaults.
    """
    if slot_name not in (SlotName.AGENT.value, SlotName.HELPER_LLM.value):
        return None
    # Function-local: free_tier imports from this package at module load.
    from narranexus.platform.agent_framework.providers.free_tier import (
        FREE_TIER_SOURCE,
        free_tier_default_models,
    )

    is_agent = slot_name == SlotName.AGENT.value
    if source == FREE_TIER_SOURCE:
        agent_model, helper_model = free_tier_default_models()
        return agent_model if is_agent else helper_model
    table = _ONBOARD_AGENT_MODELS if is_agent else _ONBOARD_HELPER_MODELS
    return table.get(source)


def get_onboard_defaults() -> dict[str, dict[str, str]]:
    """``{source: {"agent": model, "helper": model}}`` for every card SOURCE the
    platform has onboarding defaults for -- what ``GET /api/providers/catalog``
    ships so the UI never keeps its own copy of the table.

    The tables also carry the ``anthropic`` / ``openai`` provider-type keys
    (a different key space: official BYOK cards have source ``user``), which are
    not sources and are left out.
    """
    protocols = {p.value for p in ProviderProtocol}
    out: dict[str, dict[str, str]] = {}
    for key in {*_ONBOARD_AGENT_MODELS, *_ONBOARD_HELPER_MODELS} - protocols:
        agent_model = get_onboard_default_model(key, SlotName.AGENT.value)
        helper_model = get_onboard_default_model(key, SlotName.HELPER_LLM.value)
        entry = {
            name: model
            for name, model in (("agent", agent_model), ("helper", helper_model))
            if model
        }
        if entry:
            out[key] = entry
    return out


# =============================================================================
# Official Provider Detection
# =============================================================================

OFFICIAL_BASE_URLS: dict[str, set[str]] = {
    "openai": {"", "https://api.openai.com/v1", "https://api.openai.com/v1/"},
    "anthropic": {"", "https://api.anthropic.com", "https://api.anthropic.com/"},
}


def is_official_provider(protocol: str, base_url: str) -> bool:
    """Check if a base_url belongs to an official provider."""
    return base_url in OFFICIAL_BASE_URLS.get(protocol, set())


def get_official_models(protocol: str) -> list[str]:
    """Get the full model list for an official provider (OpenAI or Anthropic)."""
    return list(_SUGGESTED_MODELS.get(protocol, []))