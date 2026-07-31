"""
@file_name: prompt_cache.py
@author: Bin Liang
@date: 2026-07-29
@description: Prompt-cache policy — pure functions, no state (day-1
constraint C2: "everything protects the prefix; retrofitting is
expensive" — three harnesses' source agrees).

The loop only guarantees it never breaks the prefix itself: tools sort
deterministically (dynamic expansion appends at the tail, never
reorders), and Anthropic-style breakpoints are planned from the stable
message head. Byte-stability of the platform-built prefix is the
platform's own milestone; these functions are the loop-side half.
"""

from __future__ import annotations

from typing import Any

from xyz_agent_context.agent_framework.nexus_power.contracts.events import Usage
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    CachePlan,
    ProviderMessage,
    ProviderProfile,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import ToolSpec


def order_tools(tools: list[ToolSpec]) -> list[ToolSpec]:
    """Deterministic tool order with append-only extension.

    Base order is name-sorted; the dispatcher feeds tools in
    (registration_generation, name) buckets so later expansions append
    after the existing span instead of resorting the whole list — a
    resort would shift every schema byte and void the prefix cache.
    """
    return sorted(tools, key=lambda t: t.name)


def plan_cache(
    messages: list[ProviderMessage], profile: ProviderProfile
) -> CachePlan:
    """Choose breakpoint indices for ``breakpoints`` dialects.

    Strategy (Anthropic budget: ``profile.max_breakpoints``): one marker
    at the last system message (prompt + tools prefix), one at the last
    message before this step's dynamic tail (conversation history
    prefix). ``prefix_auto`` / ``none`` dialects return an empty plan —
    their caching (if any) rides on deterministic ordering alone.
    """
    if profile.cache_style != "breakpoints" or not messages:
        return CachePlan()
    indices: list[int] = []
    last_system = -1
    for i, message in enumerate(messages):
        if message.get("role") == "system":
            last_system = i
        else:
            break
    if last_system >= 0:
        indices.append(last_system)
    if len(messages) - 1 > last_system:
        indices.append(len(messages) - 1)
    return CachePlan(breakpoint_indices=tuple(indices[: profile.max_breakpoints]))


def cache_hit_metrics(total: Usage) -> dict[str, Any]:
    """Cache economics from real usage (consumed by cost transparency)."""
    denominator = total.input_tokens + total.cache_read_tokens
    hit_rate = (total.cache_read_tokens / denominator) if denominator else 0.0
    return {
        "cache_read_tokens": total.cache_read_tokens,
        "cache_creation_tokens": total.cache_creation_tokens,
        "uncached_input_tokens": total.input_tokens,
        "cache_hit_rate": round(hit_rate, 4),
    }
