"""
@file_name: fixtures.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: One-liner helpers for resource setup + automatic cleanup

Cases call ``ctx.fixtures.make_user()`` / ``ctx.fixtures.make_agent(user)``
instead of poking ``api_client`` directly. The fixture records every
created resource on the case ledger so the runner cleans up after the
group regardless of whether the case passed, failed, or errored.

There is no "delete" surface here. Cases never own teardown — the
runner does, via ``cleanup_ledger``.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field

from .api_client import APIClient, LocalAgent, LocalUser


# Defaults — overridable per-call. These match the slot decisions logged
# in docs/SMOKE_TEST_RESEARCH.md: DeepSeek V4 Pro on the AGENT slot,
# DeepSeek V4 Flash on the HELPER_LLM slot, both served by NetMind.
DEFAULT_AGENT_MODEL = "deepseek-ai/DeepSeek-V4-Pro"
DEFAULT_HELPER_MODEL = "deepseek-ai/DeepSeek-V4-Flash"
# NetMind OpenAI protocol's embedding model lineup. BGE-M3 is the
# multilingual default and covers the Chinese-heavy test inputs well.
DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
NETMIND_KEY_ENV_VAR = "NETMIND_API_KEY"


@dataclass
class ResourceLedger:
    """Tracks the resources a case creates so the runner can clean
    them up. Order at cleanup: agents (each via its owner) → users
    (no backend DELETE today; the prefix sweep handles them)."""

    user_ids: list[str] = field(default_factory=list)
    agents: list[tuple[str, str]] = field(default_factory=list)  # (agent_id, owner)


class CaseFixtures:
    """Owned by one CaseContext; one instance per case."""

    def __init__(
        self,
        api: APIClient,
        ledger: ResourceLedger,
        prefix: str,
    ) -> None:
        self._api = api
        self._ledger = ledger
        self._prefix = prefix

    def _new_user_id(self) -> str:
        return f"{self._prefix}_u_{uuid.uuid4().hex[:8]}"

    def _new_agent_name(self) -> str:
        return f"{self._prefix}_agent_{uuid.uuid4().hex[:6]}"

    async def make_user(self, display_name: str | None = None) -> LocalUser:
        user_id = self._new_user_id()
        user = await self._api.create_user(user_id, display_name=display_name or user_id)
        self._ledger.user_ids.append(user.user_id)
        return user

    async def make_agent(
        self,
        user: LocalUser,
        *,
        name: str | None = None,
        description: str | None = None,
        with_llm: bool = True,
        agent_model: str = DEFAULT_AGENT_MODEL,
        helper_model: str = DEFAULT_HELPER_MODEL,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    ) -> LocalAgent:
        """Create an agent for ``user``. When ``with_llm`` is true (the
        default) we also bind a NetMind provider card to the user with
        the agent + helper_llm slots pre-set, so the WS turn that
        follows actually reaches an LLM.

        The NetMind key is read from ``NETMIND_API_KEY``. The runner
        loads it from ``NarraNexus/.env`` at startup so callers don't
        have to thread it through. Missing key → we still create the
        agent but skip provider setup; the case will then surface the
        "no provider configured" failure in its transcript, which is
        the right place for that signal to live."""
        if with_llm:
            api_key = os.environ.get(NETMIND_KEY_ENV_VAR, "").strip()
            if api_key:
                await self._api.add_netmind_card(
                    user.user_id,
                    api_key,
                    agent_model=agent_model,
                    helper_model=helper_model,
                    embedding_model=embedding_model,
                )
        agent = await self._api.create_agent(
            user_id=user.user_id,
            name=name or self._new_agent_name(),
            description=description or "real_case_e2e_test agent",
        )
        self._ledger.agents.append((agent.agent_id, user.user_id))
        return agent


async def cleanup_ledger(api: APIClient, ledger: ResourceLedger) -> list[str]:
    """Best-effort cleanup. Each failure is reported as a string so the
    runner can include it in the manifest without aborting the rest."""
    failures: list[str] = []

    for agent_id, owner in ledger.agents:
        try:
            await api.delete_agent(agent_id, user_id=owner)
        except Exception as exc:
            failures.append(f"agent {agent_id}: {exc}")

    # No DELETE /users endpoint yet. Leftover users with our prefix
    # accumulate; the prefix sweep at next-run startup will list them
    # so an operator can clear by hand or via direct DB.
    if ledger.user_ids:
        failures.append(
            f"users not deleted ({len(ledger.user_ids)}): backend has no "
            f"DELETE /api/auth/users; sweep manually or add the endpoint"
        )

    return failures
