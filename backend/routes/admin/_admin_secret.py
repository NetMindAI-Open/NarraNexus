"""
@file_name: _admin_secret.py
@author: Bin Liang
@date: 2026-08-13
@description: Shared gate for the self-credentialed admin routes.

The admin routes (identity migration, runtime status, account suspension) are
driven by a private operator or a headless watcher, NOT by a user JWT. They all
authenticate the same way: an ``X-Admin-Secret`` header compared against the
platform ``admin_secret_key``. This module is the ONE copy of that check, so the
three route modules cannot drift apart — a private helper module, exactly like
``backend/routes/artifacts/_token.py``.

STATUS-CODE CONTRACT (do not change without updating the deploy alert watcher):
  * 503 — no admin secret configured. A cloud-grade deployment with the feature
    ON but no secret set is a misconfiguration, not an open door; refuse rather
    than allow. The deploy-side alert watcher reads 503 as "feature off".
  * 403 — a missing or wrong header. Authenticated-but-not-permitted.

The compare is constant-time (``hmac.compare_digest``) so the header cannot be
recovered by timing the response.
"""
from __future__ import annotations

import hmac

from fastapi import Header, HTTPException

from narranexus.platform.settings import settings


def require_admin_secret(provided: str) -> None:
    """Gate a route on the platform admin secret.

    Raises 503 when no secret is configured, 403 when the provided header is
    missing or does not match. Returns None on success.
    """
    expected = (settings.admin_secret_key or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin secret not configured")
    provided = (provided or "").strip()
    if not provided or not hmac.compare_digest(
        provided.encode(), expected.encode()
    ):
        raise HTTPException(status_code=403, detail="invalid admin secret")


async def admin_secret_dependency(x_admin_secret: str = Header(default="")) -> None:
    """``Depends``-form of :func:`require_admin_secret`.

    Declared as a dependency it is resolved BEFORE the request body is
    validated, so an unauthenticated caller gets 403 and never a 422 that
    spells out the body schema (2026-10-28 audit).
    """
    require_admin_secret(x_admin_secret)
