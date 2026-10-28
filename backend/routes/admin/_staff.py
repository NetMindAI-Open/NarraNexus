"""
@file_name: _staff.py
@author: Bin Liang
@date: 2026-10-28
@description: Shared staff-role gate for user-authenticated admin routes.

Sibling of ``_admin_secret.py``: that module gates the SELF-credentialed admin
routes (operator / watcher with ``X-Admin-Secret``); this one gates admin
routes reached with a normal user session, where the question is "is this
caller staff?". One copy so the role check cannot drift between route files.

MODE SEMANTICS (the part the copies used to disagree on):
  * local / desktop mode -> ALLOW. There are no roles; the single local user
    is the operator and the desktop System page calls these routes.
  * cloud mode -> ``request.state.role`` must be ``"staff"``, else 403. A
    missing role in cloud (``None`` / ``""``) is REFUSED: the mode decides
    "local", never the absence of a role.

Not every staff check fits this policy. ``admin/quota.py`` deliberately answers
503 outside cloud mode (quota admin has no meaning without the wallet), and
``admin/logs.py`` decides "local" from a missing role rather than from the
mode; neither is routed through here.
"""
from __future__ import annotations

from fastapi import HTTPException, Request

from narranexus.platform.utils.deployment_mode import is_cloud_mode


async def require_staff_in_cloud(request: Request) -> None:
    """Allow in local mode; in cloud mode require the staff role (403 otherwise).

    Usable directly as a dependency: ``dependencies=[Depends(require_staff_in_cloud)]``.
    """
    if not is_cloud_mode():
        return
    if getattr(request.state, "role", None) != "staff":
        raise HTTPException(status_code=403, detail="staff role required")
