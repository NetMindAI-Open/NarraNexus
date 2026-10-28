"""
@file_name: agent_discovery_sync.py
@author: NarraNexus
@date: 2026-08-04
@description: Keep an agent's peer-discovery row true — the single place that
decides what other agents can learn about it.

Why this exists (P1 段02, prod 2026-08-03)
-----------------------------------------
Two agents of the same user both showed "configuration complete" in the UI, yet
an A2A question was answered with "还没配置完成" and the inbox stayed empty. The
data layer, not the prompts, was at fault:

  * ``agents.agent_description`` was written once at creation as
    "A new agent ready for configuration" and never again — no config change,
    skill install or rename touched it;
  * ``bus_agent_registry`` was written from ONE inline block in
    ``MessageBusModule.gather``, snapshotting that placeholder and
    hardcoding ``capabilities=[]``. All 488 rows in prod looked like that;
  * so ``bus_search_agents`` (matching ``capabilities LIKE ? OR description
    LIKE ?``) answered nothing for every query, ``bus_get_agent_profile``
    reported "ready for configuration" for a configured agent, and the asking
    agent reasonably concluded the peer was not ready and refused to send
    (evt_feb1f6ae). A2A discovery was systemically dead, not flaky.

Two design rules follow from that, and they are the point of this module:

1. **Recompute, never trust a stored summary.** ``capabilities`` is derived
   from facts the platform already owns — installed skills and active module
   classes — so discovery cannot depend on an agent remembering to describe
   itself (iron rule #15) and no scenario is hardcoded (iron rule #4). A
   description the agent wrote about itself is used verbatim; the legacy
   placeholder is treated as "nothing said" and NOT republished.
2. **One seam, called from everywhere.** Creation, name/description edits,
   awareness configuration, skill install/uninstall and the per-turn bus hook
   all call this function. Registration therefore happens when an agent is
   created — not on its first turn, which is what made a configured-but-idle
   agent invisible to its peers.

   "One" is literal, and it took two removals over two review rounds to earn
   that word — the claim was written here before it was true, which is its own
   lesson:

   * the ``bus_register_agent`` MCP tool let an agent rewrite the row with
     ``owner_user_id=""``, and ``search_agents`` filters on that column, so a
     single call dropped the agent out of its own owner's search results until
     the next turn healed it. Deleted; the agent sets its name/description
     through ``update_agent_profile`` and capabilities are never self-declared.
   * ``InstanceFactory._register_agent_in_bus`` hand-rolled the same upsert
     with ``capabilities=json.dumps([])`` and its own copy of the description
     rule — the original defect, in a third file. It now calls this function,
     which also gave bundle/migration import and arena provisioning the sync
     they never had (both left an empty-capability row behind).

   If you are adding a writer: don't. Call this function.

It lives in ``message_bus/`` rather than ``services/`` because
``bus_agent_registry`` is this package's table and this is synchronous policy
called from routes, MCP tools and hooks — ``services/`` is for background
workers (pollers, sync loops, watchers).

Deliberately NOT here: generating a description with an LLM from the agent's
Awareness. That would put a paid, nondeterministic call on the create path and
on every skill install. The description is a stable self-positioning line
(bootstrap writes it, a rename or reconfiguration can update it); capabilities
carry what changes often.
"""

from typing import List, Optional

from loguru import logger

from narranexus.platform.schema import is_agent_description_unset

def _uninteresting(module_class: str) -> bool:
    """A module whose presence says nothing about what an agent can DO for a
    peer — every agent has it — declares ``discovery_hidden``; advertising it
    would be pure noise in a capability search."""
    from narranexus.platform.module_system import module_config

    cfg = module_config(module_class)
    return bool(cfg and cfg.discovery_hidden)


def _module_capability_token(module_class: str) -> str:
    """``LarkModule`` → ``lark``. A stable, lower-case token a search can hit."""
    name = module_class[:-len("Module")] if module_class.endswith("Module") else module_class
    out: List[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


async def collect_agent_capabilities(db, agent_id: str) -> List[str]:
    """The agent's capability tokens: installed skills + active module reach.

    Skills are the discovery-relevant part ("who can do X"); module classes
    describe reach ("can be reached on lark", "can run jobs"). Sorted, because
    the row is rewritten on every turn and a stable value keeps the write a
    genuine no-op.

    Never raises: a capability list is discovery metadata, and the callers are
    best-effort paths (an HTTP route creating an agent, a per-turn hook).
    """
    tokens: set[str] = set()

    try:
        rows = await db.get(
            "skill_installations", {"agent_id": agent_id, "status": "installed"}
        )
        for row in rows or []:
            skill_id = (row.get("skill_id") or "").strip()
            if skill_id:
                tokens.add(skill_id)
    except Exception as e:  # noqa: BLE001 — metadata, never flow control
        logger.debug(f"[discovery-sync] skills unavailable for {agent_id}: {e}")

    try:
        rows = await db.get(
            "module_instances", {"agent_id": agent_id, "status": "active"}
        )
        for row in rows or []:
            module_class = (row.get("module_class") or "").strip()
            if not module_class or _uninteresting(module_class):
                continue
            token = _module_capability_token(module_class)
            if token:
                tokens.add(token)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[discovery-sync] instances unavailable for {agent_id}: {e}")

    return sorted(tokens)


def build_discovery_description(agent_name: str, agent_description: Optional[str]) -> str:
    """What a peer should be told this agent is.

    ``"<name>: <description>"`` when the agent has said something about itself,
    just ``"<name>"`` when it has not. The placeholder branch matters: echoing
    "A new agent ready for configuration" is what convinced asking agents that
    a configured peer was unusable, and saying nothing is strictly better than
    asserting something false.
    """
    name = (agent_name or "").strip()
    if is_agent_description_unset(agent_description):
        return name
    return f"{name}: {(agent_description or '').strip()}" if name else (agent_description or "").strip()


async def sync_agent_discovery(db, agent_id: str) -> bool:
    """Recompute and store the agent's peer-discovery row.

    Returns True when a row was written, False when there was nothing to write
    (unknown/deleted agent) or the write failed. Never raises: every caller is
    on a path whose real job is something else (creating an agent, installing a
    skill, gathering context for a turn) and none of them may fail because
    discovery metadata could not be refreshed.
    """
    try:
        agent_row = await db.get_one("agents", {"agent_id": agent_id})
        if not agent_row:
            logger.debug(f"[discovery-sync] no agents row for {agent_id}; skipping")
            return False

        from narranexus.platform.repository.agent_registry_repository import (
            AgentRegistryRepository,
        )

        capabilities = await collect_agent_capabilities(db, agent_id)
        description = build_discovery_description(
            agent_row.get("agent_name", ""), agent_row.get("agent_description")
        )
        await AgentRegistryRepository(db).upsert_profile(
            agent_id,
            owner_user_id=agent_row.get("created_by", ""),
            capabilities=capabilities,
            description=description,
            visibility="public" if agent_row.get("is_public") else "private",
        )
        return True
    except Exception as e:  # noqa: BLE001 — see docstring
        logger.warning(f"[discovery-sync] failed for {agent_id}: {e}")
        return False
