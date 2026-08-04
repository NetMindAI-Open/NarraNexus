"""
@file_name: free_tier.py
@author: Bin Liang
@date: 2026-07-28
@description: Identity of the free-tier provider card.

The free tier is NOT a routing special case. It is an ordinary two-row provider
card (anthropic + openai, one linked group) whose key happens to be a wallet on
our LiteLLM gateway rather than a credential the user pasted. Everything
downstream — slot binding, model switching, the agent loop, cost records —
treats it exactly like the user's own NetMind card.

This module owns the three facts that make that work:

  * ``FREE_TIER_SOURCE`` — the ``user_providers.source`` value. Distinct from
    ``netmind`` on purpose: the two must be able to COEXIST, so that a user who
    burns through the wallet can switch to their own NetMind Power card without
    either row displacing the other (the one-card-per-source guard in
    ``UserProviderService`` is per source).

  * ``free_tier_endpoints()`` — the gateway URLs, read from the environment.
    They belong here rather than in ``user_service``'s module-level provider
    table because they are DEPLOYMENT-specific (dev and prod gateways differ,
    and local mode has none at all), and a module-level constant cannot express
    that.

  * ``free_tier_default_models()`` / ``free_tier_default_thinking()`` — the
    deployment-controlled opening models and agent thinking mode. The latter
    fails closed to ``off`` unless ops explicitly selects ``on`` or ``auto``.

Upstream: ``backend/integrations/free_tier/provisioner.py`` opens the wallet and
calls ``onboard_one_key(provider_type=FREE_TIER_SOURCE, ...)``.
Downstream: ``UserProviderService`` builds the rows; ``cloud_policy`` allows
them to be bound to slots.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from loguru import logger

# ``user_providers.source`` for the free-tier card. Also the ``card_type`` the
# dual-provider builder keys on, so the two can never drift.
FREE_TIER_SOURCE = "netmind_free"

# Display names. The card must read as "the platform is paying for this", not as
# a NetMind account the user owns — the two are side by side in Settings.
_CARD_NAMES = {
    "anthropic": "Free Tier (Anthropic)",
    "openai": "Free Tier (OpenAI)",
}

# Fallbacks match the compose service names, so a deployment that runs the
# standard gateway stack needs no extra env at all.
_DEFAULT_ANTHROPIC_BASE = "http://litellm:4000"
_DEFAULT_OPENAI_BASE = "http://litellm:4000/v1"

# Models the card is CREATED with. Not a restriction: the gateway serves the
# whole upstream catalogue, `model_sync` refreshes the list daily, and the user
# may switch to anything in it. These are only the sane starting slots.
_DEFAULT_AGENT_MODEL = "deepseek-ai/DeepSeek-V4-Pro"
_DEFAULT_HELPER_MODEL = "deepseek-ai/DeepSeek-V4-Flash"


@dataclass(frozen=True)
class FreeTierEndpoints:
    """Per-protocol gateway URLs for the free-tier card.

    Two different shapes because the two protocols are served differently: the
    Claude CLI appends ``/v1/messages`` to its base, while the OpenAI client
    expects the base to already end in ``/v1``. Both were validated against the
    live gateway before this design replaced the per-run-key one.
    """

    anthropic_base_url: str
    openai_base_url: str

    def for_protocol(self, protocol: str) -> str:
        return (
            self.anthropic_base_url
            if protocol == "anthropic"
            else self.openai_base_url
        )


def free_tier_endpoints() -> FreeTierEndpoints:
    return FreeTierEndpoints(
        anthropic_base_url=_env(
            "FREE_TIER_GATEWAY_ANTHROPIC_BASE_URL", _DEFAULT_ANTHROPIC_BASE
        ),
        openai_base_url=_env(
            "FREE_TIER_GATEWAY_OPENAI_BASE_URL", _DEFAULT_OPENAI_BASE
        ),
    )


def free_tier_card_name(protocol: str) -> str:
    return _CARD_NAMES.get(protocol, "Free Tier")


def free_tier_default_models() -> tuple[str, str]:
    """``(agent_model, helper_model)`` the card starts on."""
    return (
        _env("FREE_TIER_AGENT_MODEL", _DEFAULT_AGENT_MODEL),
        _env("FREE_TIER_HELPER_MODEL", _DEFAULT_HELPER_MODEL),
    )


def free_tier_default_thinking() -> str:
    """Neutral ``thinking`` value the card's agent slot starts on.

    Defaults to ``off`` — deliberately, and for two independent reasons:
    the wallet pays for reasoning tokens the user never sees, and the
    LiteLLM gateway's ``/v1/messages`` bridge drops the entire reply when
    the upstream answers with reasoning attached (BerriAI/litellm#29518,
    diagnosed 2026-08-02). Set ``FREE_TIER_AGENT_THINKING=auto`` to restore
    auto once the gateway is on a version that survives it (an EMPTY env
    value falls back to the default, so ``auto`` is the explicit escape).
    """
    value = _env("FREE_TIER_AGENT_THINKING", "off").lower()
    if value in ("on", "off"):
        return value
    if value == "auto":
        return ""
    logger.warning(
        "Unrecognised FREE_TIER_AGENT_THINKING={!r} — falling back to 'off'",
        value,
    )
    return "off"


def is_free_tier_enabled() -> bool:
    """Whether new users should be given a wallet.

    Cloud-only by construction: the wallet lives on a gateway that only exists
    in the cloud deployment. A local/desktop install never has one, which is
    why every caller can gate on this single flag.
    """
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode

    if not is_cloud_mode():
        return False
    return _env("FREE_TIER_ENABLED", "false").lower() == "true"


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default
