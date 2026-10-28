"""
Unified Agent Memory system (refactor/agent-memory, 2026-06-03).

One record shape, one Engine of fixed lifecycle steps, one Spec per kind
(mechanism vs policy). Retrieval is vector-free: BM25 + grep + structured
filters, with the LLM as the relevance arbiter.
"""
from narranexus.platform.memory.record import (
    MemoryRecord,
    new_record_id,
    SCOPE_AGENT,
    SCOPE_USER,
    SCOPE_NARRATIVE,
    SCOPE_INSTANCE,
    SCOPE_GLOBAL,
)
from narranexus.platform.memory.spec import (
    MemoryKindSpec,
    RecallWeights,
    declare_spec,
    memory_kind_registry,
    register_spec,
    get_spec,
    all_kinds,
    passive_kinds,
)
from narranexus.platform.memory.engine import MemoryEngine
from narranexus.platform.memory.coordinator import (
    MemoryCoordinator,
    MemoryHit,
    format_memory_hits,
)

# The memory kinds themselves are the builtin.memory_kinds plugin (plugins/,
# batch 6b): the host boot registers them from the builtin.memory_kinds manifest;
# nothing registers at import.

__all__ = [
    "MemoryRecord",
    "new_record_id",
    "SCOPE_AGENT",
    "SCOPE_USER",
    "SCOPE_NARRATIVE",
    "SCOPE_INSTANCE",
    "SCOPE_GLOBAL",
    "MemoryKindSpec",
    "RecallWeights",
    "register_spec",
    "declare_spec",
    "memory_kind_registry",
    "get_spec",
    "all_kinds",
    "passive_kinds",
    "MemoryEngine",
    "MemoryCoordinator",
    "MemoryHit",
    "format_memory_hits",
]
