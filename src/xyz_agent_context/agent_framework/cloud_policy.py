"""
@file_name: cloud_policy.py
@author: NarraNexus
@date: 2026-07-18
@description: Single source of truth for the cloud "netmind-only" slot policy.

On the multi-tenant cloud deployment a NON-STAFF user runs on their
NetMind ("Power") account only:

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
  - ``backend/routes/manyfold_agents.py`` filters its cross-user
    provider clone through :func:`netmind_slots_only`.
  - Frontend twin: ``frontend/src/lib/agentFramework.ts``
    ``cloudNetmindOnly()`` — keeps the dropdowns from offering choices
    these checks would reject.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from xyz_agent_context.utils.deployment_mode import is_cloud_mode

NETMIND_SOURCE = "netmind"

NETMIND_ONLY_DETAIL = (
    "Cloud accounts run on your NetMind account — using your own API-key "
    "providers is available in the local (desktop) version only."
)

FRAMEWORK_LOCKED_DETAIL = (
    "Switching the agent framework is staff-only in cloud mode — the cloud "
    "version runs on your NetMind account. Framework switching is available "
    "in the local (desktop) version."
)


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
    if netmind_slots_only(actor_is_staff) and prov.get("source") != NETMIND_SOURCE:
        raise CloudPolicyViolation(NETMIND_ONLY_DETAIL)
