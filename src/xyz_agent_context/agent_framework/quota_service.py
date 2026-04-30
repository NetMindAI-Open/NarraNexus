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

from typing import Awaitable, Callable, Optional

from loguru import logger

from xyz_agent_context.schema.quota_schema import Quota
from xyz_agent_context.repository.quota_repository import QuotaRepository
from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)


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
        repo: Optional[QuotaRepository],
        system_provider: SystemProviderService,
        repo_getter: Optional[Callable[[], Awaitable[QuotaRepository]]] = None,
    ):
        if repo is None and repo_getter is None:
            raise ValueError("QuotaService requires either repo or repo_getter")
        self.repo = repo
        self.system_provider = system_provider
        self._repo_getter = repo_getter

    async def _get_repo(self) -> QuotaRepository:
        if self._repo_getter is not None:
            return await self._repo_getter()
        assert self.repo is not None
        return self.repo

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
        repo = await self._get_repo()
        existing = await repo.get_by_user_id(user_id)
        if existing is not None:
            return existing
        inp, out = self.system_provider.get_initial_quota()
        try:
            return await repo.create(user_id, inp, out)
        except Exception as e:
            logger.exception(f"init_for_user failed for {user_id}: {e}")
            return None

    async def check(self, user_id: str) -> bool:
        if not self.system_provider.is_enabled():
            return False
        repo = await self._get_repo()
        try:
            q = await repo.get_by_user_id(user_id)
        except Exception as e:
            logger.exception(f"quota check db error for {user_id}: {e}")
            return False
        return q is not None and q.has_budget()

    async def deduct(
        self, user_id: str, input_tokens: int, output_tokens: int
    ) -> None:
        if not self.system_provider.is_enabled():
            return
        if input_tokens <= 0 and output_tokens <= 0:
            return
        repo = await self._get_repo()
        try:
            await repo.atomic_deduct(user_id, input_tokens, output_tokens)
        except Exception as e:
            logger.exception(f"quota deduct failed for {user_id}: {e}")

    async def get(self, user_id: str) -> Optional[Quota]:
        repo = await self._get_repo()
        return await repo.get_by_user_id(user_id)

    async def grant(
        self, user_id: str, input_tokens: int, output_tokens: int
    ) -> Quota:
        """Staff grant. Upserts: if the target has no row, creates one with
        initial=0 so the grant credits land immediately.
        """
        repo = await self._get_repo()
        existing = await repo.get_by_user_id(user_id)
        if existing is None:
            await repo.create(user_id, 0, 0)
        await repo.atomic_grant(user_id, input_tokens, output_tokens)
        result = await repo.get_by_user_id(user_id)
        assert result is not None
        return result

    async def set_preference(
        self, user_id: str, prefer_system_override: bool
    ) -> Quota:
        """User toggles whether to force-route through the system-default
        provider (free tier) instead of their own. Upserts: if no row
        exists for this user, creates one with initial=0 so the toggle
        value is persisted — subsequent budget lookups return the normal
        has_budget() answer (false, since 0 + 0 = 0 remaining).
        """
        repo = await self._get_repo()
        existing = await repo.get_by_user_id(user_id)
        if existing is None:
            await repo.create(user_id, 0, 0)
        await repo.set_preference(user_id, prefer_system_override)
        result = await repo.get_by_user_id(user_id)
        assert result is not None
        return result


async def bootstrap_quota_subsystem(db) -> QuotaService:
    """Initialise the QuotaService.default() singleton for a process.

    backend.main's FastAPI lifespan already wires this for the HTTP
    process; any separate long-running entry point (job_trigger,
    bus_trigger, lark trigger, MCP runner when spawned standalone)
    must call this at startup so QuotaService.default() resolves and
    AgentRuntime's fallback to the system-default quota can fire.

    Idempotent: replaces the current singleton if already set.
    """
    from xyz_agent_context.repository.quota_repository import QuotaRepository
    from xyz_agent_context.utils.db_factory import get_db_client

    sys_provider = SystemProviderService.instance()

    # The QuotaService singleton is process-wide, but MCP runs multiple
    # module servers in separate event loops inside one process. Holding a
    # repo/db from the bootstrap loop would leak an aiomysql pool across
    # loops and surface as "Future attached to a different loop". Always
    # resolve the repo from the current loop on demand.
    async def _current_loop_repo() -> QuotaRepository:
        current_db = await get_db_client()
        return QuotaRepository(current_db)

    svc = QuotaService(
        repo=None,
        system_provider=sys_provider,
        repo_getter=_current_loop_repo,
    )
    QuotaService.set_default(svc)
    return svc
