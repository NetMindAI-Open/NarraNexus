"""
@file_name: power_account.py
@author: NarraNexus
@date: 2026-07-13
@description: Per-user predicate — is this user a NetMind ("Power") account?

This is the per-user half of the "power" axis (see
``utils/deployment_mode.py`` for the deployment-level half,
``is_power_login_enabled()``). It answers: for THIS user_id, are the NetMind
account features (billing / subscription / recharge, the Account & Subscription
panel) available?

A user is a Power account iff their ``users`` row has ``user_type ==
"individual"`` — the type stamped by ``UserRepository.upsert_netmind_user`` on
NetMind login. Pure-local username users are ``user_type == "local"`` and get a
clean 404 from the billing routes.

It lives here (not in ``deployment_mode.py``) because it needs a DB read; the
deployment_mode module stays a pure, synchronous env leaf. Fails closed: a
falsy user_id, a missing row, or any non-"individual" type returns False, so a
gate built on it denies rather than leaks.
"""
from __future__ import annotations

from xyz_agent_context.utils.db_factory import get_db_client


async def is_power_account(user_id: str) -> bool:
    """True iff ``user_id`` names an existing NetMind (``individual``) user.

    Args:
        user_id: The resolved current-user id (from auth_middleware).

    Returns:
        True when the users row exists and ``user_type == "individual"``;
        False otherwise (missing id, missing row, or a local user).
    """
    if not user_id:
        return False
    db = await get_db_client()
    row = await db.get_one("users", {"user_id": user_id})
    return bool(row) and row.get("user_type") == "individual"
