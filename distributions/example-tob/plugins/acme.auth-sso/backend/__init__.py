"""Acme SSO — example authProviders stub. Replace ``authenticate`` with a real SSO/OIDC check."""
from __future__ import annotations

from typing import Any, Mapping

from narranexus.kernel.plugins.registry import Contribution


class AcmeSsoProvider:
    id = "acme.auth-sso"
    scheme = "bearer"

    async def authenticate(self, request: Any) -> Mapping[str, Any] | None:
        header = request.headers.get("Authorization", "") or ""
        if not header.startswith("Bearer acme-"):
            return None
        return {"user_id": header[len("Bearer acme-"):], "role": "user", "provider": self.id}


CONTRIBUTION = Contribution("acme_sso", lambda: AcmeSsoProvider())
