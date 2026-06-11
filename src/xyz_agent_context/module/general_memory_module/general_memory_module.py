"""
@file_name: general_memory_module.py
@author: NetMind.AI
@date: 2026-06-03
@description: GeneralMemoryModule — the agent's general "learning" memory.

Unlike Chat (history), Social (entities) or Narrative (sessions), this module
distils each interaction into discrete, durable OBSERVATIONS — objective WORLD
facts ("Alice works at Google") and subjective EXPERIENCE facts ("this user
prefers terse replies"). It is a thin adapter over the unified MemoryEngine:

  - hook_after_event_execution  → extract facts (LLM) → engine.retain(observation)
                                   (raw facts; the background worker consolidates them)
  - hook_data_gathering         → engine.recall(observation, current input) → ctx

All persistence/dedup/consolidation/recall live in the engine — this module
only owns the extraction prompt wiring and the prompt-injection rendering.
A-path boundary (design §7.3): it does NOT touch Social entities or Awareness.
"""
from __future__ import annotations

from typing import List, Optional

from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK
from xyz_agent_context.memory import MemoryCoordinator, MemoryEngine, MemoryRecord, SCOPE_AGENT, get_spec, passive_kinds
from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.schema.context_schema import ContextData
from xyz_agent_context.schema.hook_schema import HookAfterExecutionParams
from xyz_agent_context.schema.module_schema import ModuleConfig, MCPServerConfig

# MCP port for the remember / grep_memory tools. Registered in
# module_runner CORE_MODULE_PORTS. 7809 = next free after BasicInfo(7808).
_MCP_PORT = 7809

_RECALL_LIMIT = 8
_RECALL_TOKENS = 800
_VALID_SUBTYPES = {"world", "experience"}


def _recalled_at(record: MemoryRecord) -> str:
    """A short 'when learned' stamp for a recalled memory.

    A memory about the same thing can change over time (an observation is
    re-stated, a fact is updated). We do NOT yet do supersession/update — the
    real merge path is deferred (design). As an interim, we surface each
    memory's timestamp so the agent can prefer the most recent when two
    memories conflict. `updated_at` (last time the record changed) falls back
    to `created_at` (when first learned); both are UTC, shown to the minute —
    enough to tell which of two memories is newer.
    """
    ts = record.updated_at or record.created_at
    return f"({ts.strftime('%Y-%m-%d %H:%M')})" if ts else "(time unknown)"


class _Fact(BaseModel):
    text: str = Field(description="One clear observation sentence.")
    kind: str = Field(description="'world' (about others/the world) or 'experience' (the agent's own action/judgment).")


class _Extracted(BaseModel):
    facts: List[_Fact] = Field(default_factory=list)


class GeneralMemoryModule(XYZBaseModule):
    """Capability module: always loaded; learns observations every turn."""

    def __init__(self, agent_id, user_id=None, database_client=None, instance_id=None, instance_ids=None):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = _MCP_PORT

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="GeneralMemoryModule",
            priority=2,
            enabled=True,
            description="Learns and recalls general objective/subjective memories (observations).",
            module_type="capability",
        )

    def _engine(self) -> MemoryEngine:
        return MemoryEngine(self.db, self.agent_id)

    # ── read: inject relevant unified memory into context ───────────────────
    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        """Recall across ALL memory kinds (observations, entities, chat,
        narratives, jobs, bus) relevant to the current input and inject them.
        This is the single point where the unified memory feeds the agent loop
        — migrated history + live observations alike — so the agent's context
        is drawn from one memory, not per-module silos."""
        try:
            coord = MemoryCoordinator(self._engine())
            # Passive injection draws ONLY from distilled-knowledge kinds
            # (observation/entity/narrative). Raw/voluminous kinds (chat/event/
            # job/bus) are excluded here — they are searchable via the remember
            # TOOL, and recent chat/jobs are already injected by their own
            # modules. This is the fix for the chat-echo pollution (design §4/§5).
            hits = await coord.remember(
                ctx_data.input_content or "", kinds=passive_kinds(),
                limit=_RECALL_LIMIT, token_budget=_RECALL_TOKENS,
            )
            ctx_data.extra_data["relevant_memories"] = [
                f"[{h.kind}] {_recalled_at(h.record)} {h.record.content_text}" for h in hits
            ]
        except Exception as e:  # noqa: BLE001 — memory recall must never break a turn
            logger.warning(f"GeneralMemoryModule.hook_data_gathering: recall failed: {e}")
        return ctx_data

    async def get_instructions(self, ctx_data: ContextData) -> str:
        memories = ctx_data.extra_data.get("relevant_memories") or []
        if not memories:
            return ""
        body = "\n".join(f"- {m}" for m in memories)
        return (
            "## What you remember\n"
            "Things you have learned that may be relevant now. Each item is tagged "
            "with when you learned it (UTC); when two memories about the same thing "
            "disagree, trust the most recent one.\n"
            f"{body}\n"
        )

    # ── write: distil this turn into observations (background-heavy hook) ────
    async def hook_after_event_execution(self, params: HookAfterExecutionParams) -> None:
        user_input = params.io_data.input_content or ""
        agent_output = params.io_data.final_output or ""
        event_id = params.execution_ctx.event_id
        if not user_input.strip() and not agent_output.strip():
            return
        try:
            facts = await self._extract_facts(user_input, agent_output)
            engine = self._engine()
            for fact in facts:
                subtype = fact.kind.strip().lower()
                if subtype not in _VALID_SUBTYPES or not fact.text.strip():
                    continue
                await engine.retain(MemoryRecord(
                    agent_id=self.agent_id, scope_type=SCOPE_AGENT, kind="observation",
                    subtype=subtype, content_text=fact.text.strip(),
                    source_ids=[event_id] if event_id else [], proof_count=1,
                ))
        except Exception as e:  # noqa: BLE001 — extraction is best-effort enrichment
            logger.warning(f"GeneralMemoryModule.hook_after_event_execution: extract failed: {e}")

    async def _extract_facts(self, user_input: str, agent_output: str) -> List[_Fact]:
        prompt = get_spec("observation").extract_prompt or ""
        payload = f"USER said:\n{user_input}\n\nAGENT did/said:\n{agent_output}"
        result = await OpenAIAgentsSDK().llm_function(
            instructions=prompt, user_input=payload, output_type=_Extracted, agent_id=self.agent_id,
        )
        return result.final_output.facts

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        """Hosts the agent-wide `remember` / `grep_memory` tools."""
        return MCPServerConfig(
            server_name="general_memory_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[object]:
        from xyz_agent_context.module.general_memory_module._general_memory_mcp_tools import (
            create_general_memory_mcp_server,
        )
        return create_general_memory_mcp_server(self.port)
