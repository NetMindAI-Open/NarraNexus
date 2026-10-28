"""
@file_name: cloud_policy.py
@author: NarraNexus
@date: 2026-07-18
@description: Single source of truth for the cloud "netmind-only" slot policy.

On the multi-tenant cloud deployment a NON-STAFF user runs on NetMind
capacity only — either their own ("Power") account, or the platform-funded
free-tier wallet, which is the same upstream reached through our gateway:

  - Bring-your-own API-key providers can be REGISTERED (the credential
    wallet stays open) but not BOUND to a slot — binding is what makes a
    provider drive real runs. Own keys are a local/desktop feature.
  - The agent framework cannot be changed to one that could ride the
    image's shared CLI login: the user-level switch is staff-only (gated
    in backend/routes/providers.py) and a per-agent pin to a DIFFERENT,
    non-allowed framework is rejected here. Which frameworks qualify is
    derived from the framework registry, not a list in this file.

Staff keeps full provider/framework choice (same exemption as the older
OAuth credential-riding gates); local deployments are never gated.

Consumers — keep them on THIS module, never re-derive the rule inline
(the manyfold clone gap that motivated this file happened exactly
because the rule lived in two route files and nowhere shared):

  - ``UserProviderService.set_slot`` / ``AgentSlotService.set_agent_slot``
    call :func:`ensure_slot_provider_allowed` (and the framework pin
    check) and raise :class:`CloudPolicyViolation`; routes map it to 403.
  - ``backend/routes/providers.py`` uses :func:`netmind_slots_only` for
    the register-only onboard (``activate=False``) and the
    ``default_slots`` skip.
  - ``backend/routes/manyfold/agents.py`` filters its cross-user
    provider clone through :func:`netmind_slots_only`.
  - Frontend twin: ``frontend/src/lib/agentFramework.ts``
    ``cloudNetmindOnly()`` — keeps the dropdowns from offering choices
    these checks would reject.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

from narranexus.platform.agent_framework.loop.driver import framework_metas
from narranexus.platform.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from narranexus.platform.utils.deployment_mode import is_cloud_mode

NETMIND_SOURCE = "netmind"

# Sources a cloud non-staff user may BIND to a slot. Both are NetMind capacity:
# `netmind` is the user's own Power account, `netmind_free` is the platform's
# free-tier wallet on our gateway (which forwards to the very same upstream).
# Keeping them as a set — rather than one constant plus an `or` at each call
# site — is what stopped this rule from being re-derived inline again.
CLOUD_BINDABLE_SOURCES = frozenset({NETMIND_SOURCE, FREE_TIER_SOURCE})

NETMIND_ONLY_DETAIL = (
    "Cloud accounts run on NetMind capacity — the free tier or your own "
    "NetMind account. Using your own API-key providers is available in the "
    "local (desktop) version only."
)

# Which frameworks a cloud non-staff user may select is DERIVED, not a name
# table (the frozenset that used to live here meant a third-party framework
# could never be selected on cloud no matter how it authenticates).
#
# The hazard this gate exists for is CREDENTIAL RIDING, not framework variety:
# a framework whose CLI reads a credential FILE from HOME
# (~/.claude/.credentials.json, ~/.codex/auth.json) is a hazard in the cloud
# image, which runs one `app` user with one HOME staged by a staff login — a
# non-staff user selecting it would consume staff's quota under staff's
# identity. That fact is the framework's own
# ``FrameworkMeta.uses_shared_cli_login`` (default True = fail-closed: a
# framework that never thought about the question is treated as a rider).
#
# NexusPower declares False by construction: it drives the provider API
# directly with the key of the card bound to the agent slot, and REFUSES
# subscription OAuth credentials outright (see
# `adapter/nexus_agent._resolve_provider`). A cloud non-staff user can only
# bind NetMind capacity (CLOUD_BINDABLE_SOURCES), so running it means running
# on their own account — exactly what this policy wants.
#
# The second half is an OPERATOR decision and must NEVER be attestable by the
# plugin (a `meta["cloud_safe"] = True` would be fail-open by construction):
# a rider framework is still offered when the operator provisions each cloud
# user their own card for it. `claude_code` is that case today — cloud
# provisions a per-user API-key NetMind card, and it is the OAuth CARD that
# stays staff-only (see `_OAUTH_CARD_TYPES` in backend/routes/providers.py).
# The exemption list is operator-owned config, read from the environment so a
# deployment can widen or (safely) empty it without a code change.
ENV_CLI_LOGIN_EXEMPT = "CLOUD_CLI_LOGIN_EXEMPT_FRAMEWORKS"

_DEFAULT_CLI_LOGIN_EXEMPT: frozenset[str] = frozenset({"claude_code"})


def cli_login_exempt_frameworks() -> frozenset[str]:
    """Rider frameworks the OPERATOR nonetheless offers to cloud non-staff.

    Comma-separated ``CLOUD_CLI_LOGIN_EXEMPT_FRAMEWORKS`` overrides the
    default; an empty value means "no exemptions" (the strictest setting), so
    an operator can close this door without editing code. Never read from a
    plugin: adding a framework here asserts the operator provisions per-user
    credentials for it, which the framework cannot attest about itself.
    """
    raw = os.getenv(ENV_CLI_LOGIN_EXEMPT)
    if raw is None:
        return _DEFAULT_CLI_LOGIN_EXEMPT
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def cloud_allowed_frameworks() -> tuple[str, ...]:
    """The registered frameworks a cloud non-staff user may select, in
    registration order — derived from each one's ``FrameworkMeta``."""
    return tuple(
        meta.name
        for meta in framework_metas()
        if not meta.uses_shared_cli_login or meta.name in cli_login_exempt_frameworks()
    )


def framework_locked_detail() -> str:
    """The 403 body for a framework cloud refuses, naming the ones it allows.

    Built from the allowed frameworks' DISPLAY names at call time: the prose
    used to hardcode "Claude Code or NexusPower", so a distribution that
    shipped a different set told its users the wrong thing.
    """
    allowed = [meta.display_name for meta in framework_metas() if meta.name in cloud_allowed_frameworks()]
    if allowed:
        offer = f"Cloud accounts can use {', '.join(allowed)}"
    else:
        offer = "No agent framework is available to cloud accounts on this deployment"
    return (
        "This agent framework is staff-only in cloud mode: it authenticates "
        f"through a shared CLI login rather than your own provider key. {offer}; "
        "the others are available in the local (desktop) version."
    )


def framework_allowed_in_cloud(framework: str, actor_is_staff: bool) -> bool:
    """May this actor select ``framework``?

    Staff keeps full choice; local deployments are never gated. For a
    cloud non-staff actor the answer is derived from the framework's own
    ``FrameworkMeta.uses_shared_cli_login`` plus the operator exemption list
    — see the rationale above :func:`cloud_allowed_frameworks`.

    Fail-closed on a name the registry does not know: an unregistered or
    misbound framework is refused, never let through for being new.
    """
    if not netmind_slots_only(actor_is_staff):
        return True
    return (framework or "").strip().lower() in cloud_allowed_frameworks()


class CloudPolicyViolation(Exception):
    """A slot write the cloud netmind-only policy forbids.

    Routes translate this to HTTP 403 (policy), as opposed to the
    writers' ``ValueError`` (bad input → 400).
    """


def netmind_slots_only(actor_is_staff: bool) -> bool:
    """Deployment × role: may this actor only bind NetMind-source providers?"""
    return is_cloud_mode() and not actor_is_staff


def ensure_slot_provider_allowed(
    prov: Optional[Dict[str, Any]], actor_is_staff: Optional[bool]
) -> None:
    """Raise :class:`CloudPolicyViolation` if binding ``prov`` is forbidden.

    ``actor_is_staff=None`` means a trusted internal caller (onboard,
    OAuth auto-bind, provisioner) whose policy decision was already made
    upstream — no check. Both slot writers take ``actor_is_staff`` as a
    REQUIRED keyword, so ``None`` is always an explicit, reviewable choice
    at the call site — a new caller cannot bypass the policy by simply
    forgetting the parameter. ``prov=None`` (row not found) also passes:
    the writer owns its own not-found error.
    """
    if actor_is_staff is None or prov is None:
        return
    if (
        netmind_slots_only(actor_is_staff)
        and prov.get("source") not in CLOUD_BINDABLE_SOURCES
    ):
        raise CloudPolicyViolation(NETMIND_ONLY_DETAIL)
