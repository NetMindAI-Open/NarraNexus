"""
@file_name: policy_store.py
@date: 2026-09-23
@description: Atomic owner-managed browser permissions without whole-document overwrites.

``set_rule`` is a read-modify-write of the whole stored document, made safe
twice over. Writers in this process are serialised per agent — they would
otherwise all read the same snapshot and only one per round could win, so N
concurrent clicks needed N rounds (measured on MySQL: six writers exhausted a
five-attempt bound). Across processes the write is a compare-and-swap: it
lands only if the stored JSON is byte-for-byte what was read (``BINARY``, so
MySQL does not compare under a case-insensitive collation; the SQLite backend
strips the keyword). A lost cross-process race re-reads and retries a bounded
number of times, then fails as a retryable error — never a silent "saved"
that did not happen, never a spin.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import weakref
from dataclasses import replace
from urllib.parse import urlparse

from narranexus.platform.browser._browser_impl.policy import (
    BrowserPolicy, OriginPolicy, decide, origin_of,
)
from loguru import logger

from narranexus.platform.utils.timezone import utc_now

_CAPABILITIES = ("full_cdp_access",)

#: Compare-and-swap attempts before giving up. Each lost race means another
#: writer committed, so this bounds contention, not progress.
SET_RULE_ATTEMPTS = 5


class PolicyWriteConflict(RuntimeError):
    """Concurrent writers kept winning; the caller should retry."""


class _AgentWriteLocks:
    """Per-agent write locks that exist only while someone holds or awaits one.

    Reference-counted so a long-lived process does not keep a lock for every
    agent that ever edited its permissions. Lives per event loop (an asyncio
    lock belongs to the loop it was first used on).
    """

    def __init__(self) -> None:
        self._entries: dict[str, list] = {}  # agent_id -> [lock, users]

    @contextlib.asynccontextmanager
    async def hold(self, agent_id: str):
        entry = self._entries.setdefault(agent_id, [asyncio.Lock(), 0])
        entry[1] += 1
        try:
            async with entry[0]:
                yield
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self._entries[agent_id]


#: One lock table per event loop; weak so a finished loop (tests, a restarted
#: worker) takes its table with it.
_WRITE_LOCKS: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _AgentWriteLocks]" = (
    weakref.WeakKeyDictionary()
)


def _write_lock(agent_id: str):
    return _WRITE_LOCKS.setdefault(asyncio.get_running_loop(), _AgentWriteLocks()).hold(agent_id)


class PolicyValidationError(ValueError):
    """Invalid user input, distinct from corrupt persisted policy data."""


def managed_origin(value: str) -> str:
    """Reject paths/credentials rather than silently grant a wider origin."""
    origin = origin_of(value)
    if origin is None:
        raise PolicyValidationError("An HTTP or HTTPS origin is required")
    parsed = urlparse(value.strip())
    host = parsed.hostname or ""
    if (parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path not in ("", "/") or any(char.isspace() for char in host)
            or "*" in host.removeprefix("*.")):
        raise PolicyValidationError("Use an origin without credentials, path, query or fragment")
    return origin


class PolicyStore:
    """Expose implemented permissions and change one rule with compare-and-swap."""

    TABLE = "instance_browser_policies"

    def __init__(self, db) -> None:
        self._db = db

    async def get_public(self, agent_id: str) -> dict:
        raw = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
        policy = BrowserPolicy.from_dict(json.loads(raw["policy_json"])) if raw else BrowserPolicy()
        defaults = {capability: policy.default_verdict(capability) for capability in _CAPABILITIES}
        rows = []
        for origin, entry in sorted(policy.origins.items()):
            if origin_of(origin) is None or entry.full_cdp_access is None:
                continue
            rows.append({"origin": origin, **{
                capability: decide(policy, url=origin, capability=capability).verdict
                for capability in _CAPABILITIES
            }})
        return {"agent_id": agent_id, "defaults": defaults, "origins": rows}

    async def set_rule(self, agent_id: str, *, origin: str, capability: str, verdict: str) -> dict:
        origin = managed_origin(origin)
        if capability not in _CAPABILITIES or verdict not in ("allow", "deny"):
            raise PolicyValidationError("Unsupported browser capability or verdict")
        async with _write_lock(agent_id):
            return await self._swap_rule(agent_id, origin=origin, capability=capability, verdict=verdict)

    async def _ensure_row(self, agent_id: str) -> None:
        """Create the agent's document if it has none. A concurrent creator
        winning the insert is the expected race and is not an error; anything
        else that fails here also leaves no row, so it is re-raised."""
        if await self._db.get_one(self.TABLE, {"agent_id": agent_id}) is not None:
            return
        now = utc_now().isoformat()
        try:
            await self._db.insert(self.TABLE, {
                "agent_id": agent_id, "policy_json": json.dumps(BrowserPolicy().to_dict()),
                "created_at": now, "updated_at": now,
            })
        except Exception as exc:
            if await self._db.get_one(self.TABLE, {"agent_id": agent_id}) is None:
                raise
            logger.debug("Browser policy row for {} created concurrently ({})", agent_id, type(exc).__name__)

    async def _swap_rule(self, agent_id: str, *, origin: str, capability: str, verdict: str) -> dict:
        await self._ensure_row(agent_id)
        for attempt in range(1, SET_RULE_ATTEMPTS + 1):
            raw = await self._db.get_one(self.TABLE, {"agent_id": agent_id})
            if raw is None:
                raise RuntimeError(f"Browser policy row for {agent_id} disappeared during an update")
            policy = BrowserPolicy.from_dict(json.loads(raw["policy_json"]))
            policy.origins[origin] = replace(policy.origins.get(origin, OriginPolicy()), **{capability: verdict})
            # The whole document is rewritten from the model, so keys nothing
            # reads any more do not ride along forever.
            written = await self._db.execute(
                "UPDATE instance_browser_policies SET policy_json = %s, updated_at = %s "
                "WHERE BINARY agent_id = BINARY %s AND BINARY policy_json = BINARY %s",
                (json.dumps(policy.to_dict()), utc_now().isoformat(), agent_id, raw["policy_json"]), fetch=False,
            )
            if written:
                return await self.get_public(agent_id)
            await asyncio.sleep(0.01 * attempt)
        raise PolicyWriteConflict("Browser permissions changed concurrently; retry")
