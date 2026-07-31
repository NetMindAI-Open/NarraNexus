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
  - The agent framework cannot be changed: the user-level switch is
    staff-only (gated in backend/routes/providers.py) and a per-agent
    pin to a DIFFERENT framework is rejected here.

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

from typing import Any, Dict, Optional

from xyz_agent_context.agent_framework.providers.free_tier import FREE_TIER_SOURCE
from xyz_agent_context.utils.deployment_mode import is_cloud_mode

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

# Frameworks a cloud non-staff user may select.
#
# The hazard this gate exists for is CREDENTIAL RIDING, not framework
# variety: `claude_code` and `codex_cli` authenticate through a CLI that
# reads a credential FILE from HOME (~/.claude/.credentials.json,
# ~/.codex/auth.json), and the cloud image runs one `app` user with one
# HOME — so those files are container-global and staged by a staff login.
# A non-staff user switching to such a framework would consume staff's
# quota under staff's identity. `claude_code` is nonetheless allowed
# because cloud provisions each user an API-key NetMind card for it; it is
# the OAuth CARD that stays staff-only (see `_OAUTH_CARD_TYPES` in
# backend/routes/providers.py).
#
# NexusPower cannot ride those files by construction: it drives the
# provider API directly with the key of the card bound to the agent slot,
# and REFUSES subscription OAuth credentials outright (see
# `adapters/nexus/nexus_agent._resolve_provider`). A cloud non-staff user
# can only bind NetMind capacity (CLOUD_BINDABLE_SOURCES), so running it
# means running on their own account — exactly what this policy wants.
#
# Adding a framework here is a security decision: it belongs only if the
# framework can NEVER reach a shared credential file.
CLOUD_ALLOWED_FRAMEWORKS = frozenset({"claude_code", "nexus_power"})

FRAMEWORK_LOCKED_DETAIL = (
    "This agent framework is staff-only in cloud mode: it authenticates "
    "through a shared CLI login rather than your own provider key. Cloud "
    "accounts can use Claude Code or NexusPower; the others are available "
    "in the local (desktop) version."
)


def framework_allowed_in_cloud(framework: str, actor_is_staff: bool) -> bool:
    """May this actor select ``framework``?

    Staff keeps full choice; local deployments are never gated. For a
    cloud non-staff actor the answer is membership of
    :data:`CLOUD_ALLOWED_FRAMEWORKS` — see the rationale there.
    """
    if not netmind_slots_only(actor_is_staff):
        return True
    return framework in CLOUD_ALLOWED_FRAMEWORKS


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
