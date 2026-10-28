"""
@file_name: provider.py
@author: Bin Liang
@date: 2026-09-04
@description: `kernel.auth` provider of the local/desktop distribution: a request is the user the frontend names in `X-User-Id` (the local build has no login; the header is the session). Missing header = no identity; the middleware decides what that means for the path.
"""
from __future__ import annotations

from typing import Any, Mapping

from narranexus.kernel.plugins.registry import Contribution

HEADER = "x-user-id"


class LocalAuthProvider:
    """``AuthProvider`` for the local build: identity = the ``X-User-Id`` header."""

    id = "builtin.auth.local"
    scheme = "header"

    async def authenticate(self, request: Any) -> Mapping[str, Any] | None:
        user_id = (request.headers.get(HEADER) or "").strip()
        if not user_id:
            return None
        return {"user_id": user_id, "role": "user", "provider": self.id}


CONTRIBUTION = Contribution("local", lambda: LocalAuthProvider())

__all__ = ["CONTRIBUTION", "HEADER", "LocalAuthProvider"]
