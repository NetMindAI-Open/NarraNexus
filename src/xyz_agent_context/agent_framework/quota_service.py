"""
@file_name: quota_service.py
@author: Bin Liang
@date: 2026-04-16
@description: Business orchestration for per-user free-tier token budgets.

Every public method honours SystemProviderService.is_enabled(): when the
feature is disabled (local mode or env not set), init_for_user returns
None, check returns False, and deduct silently returns. Callers never
need to guard.
"""
from __future__ import annotations

import logging
from typing import Optional

from xyz_agent_context.schema.quota_schema import Quota
from xyz_agent_context.repository.quota_repository import QuotaRepository
from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)

logger = logging.getLogger(__name__)


class QuotaService:
    """Business layer above QuotaRepository.

    Exposed method contract when the system feature is DISABLED:
    - init_for_user → None
    - check          → False (no budget to grant)
    - deduct         → silent return (no-op)
    - get            → unchanged (reading rows is always safe)
    - grant          → unchanged (staff operation, bypasses gate)
    """

    _default: Optional["QuotaService"] = None

    def __init__(
        self,
        repo: QuotaRepository,
        system_provider: SystemProviderService,
    ):
        self.repo = repo
        self.system_provider = system_provider

    @classmethod
    def set_default(cls, svc: "QuotaService") -> None:
        """Register the live instance so cost_tracker's hook can reach it."""
        cls._default = svc

    @classmethod
    def default(cls) -> "QuotaService":
        if cls._default is None:
            raise RuntimeError(
                "QuotaService.default() not initialized. "
                "backend.main lifespan should call QuotaService.set_default()."
            )
        return cls._default

    async def init_for_user(self, user_id: str) -> Optional[Quota]:
        if not self.system_provider.is_enabled():
            return None
        existing = await self.repo.get_by_user_id(user_id)
        if existing is not None:
            return existing
        inp, out = self.system_provider.get_initial_quota()
        try:
            return await self.repo.create(user_id, inp, out)
        except Exception as e:
            logger.error(f"init_for_user failed for {user_id}: {e}")
            return None

    async def check(self, user_id: str) -> bool:
        if not self.system_provider.is_enabled():
            return False
        try:
            q = await self.repo.get_by_user_id(user_id)
        except Exception as e:
            logger.error(f"quota check db error for {user_id}: {e}")
            return False
        return q is not None and q.has_budget()

    async def deduct(
        self, user_id: str, input_tokens: int, output_tokens: int
    ) -> None:
        if not self.system_provider.is_enabled():
            return
        if input_tokens <= 0 and output_tokens <= 0:
            return
        try:
            await self.repo.atomic_deduct(user_id, input_tokens, output_tokens)
        except Exception as e:
            logger.error(f"quota deduct failed for {user_id}: {e}")

    async def get(self, user_id: str) -> Optional[Quota]:
        return await self.repo.get_by_user_id(user_id)

    async def grant(
        self, user_id: str, input_tokens: int, output_tokens: int
    ) -> Quota:
        """Staff grant. Upserts: if the target has no row, creates one with
        initial=0 so the grant credits land immediately.
        """
        existing = await self.repo.get_by_user_id(user_id)
        if existing is None:
            await self.repo.create(user_id, 0, 0)
        await self.repo.atomic_grant(user_id, input_tokens, output_tokens)
        result = await self.repo.get_by_user_id(user_id)
        assert result is not None
        return result
