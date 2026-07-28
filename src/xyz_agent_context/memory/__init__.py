"""
Unified Agent Memory system (refactor/agent-memory, 2026-06-03).

One record shape, one Engine of fixed lifecycle steps, one Spec per kind
(mechanism vs policy). Retrieval is vector-free: BM25 + grep + structured
filters, with the LLM as the relevance arbiter.
"""
from xyz_agent_context.memory.record import (
    MemoryRecord,
    new_record_id,
    SCOPE_AGENT,
    SCOPE_USER,
    SCOPE_NARRATIVE,
    SCOPE_INSTANCE,
    SCOPE_GLOBAL,
)
from xyz_agent_context.memory.spec import (
    MemoryKindSpec,
    RecallWeights,
    register_spec,
    get_spec,
    all_kinds,
    passive_kinds,
)
from xyz_agent_context.memory.engine import MemoryEngine
from xyz_agent_context.memory.coordinator import MemoryCoordinator, MemoryHit

# Import for side effect: registering all kind specs. Importing the memory
# package (directly or via any submodule) therefore guarantees every kind's
# MemoryKindSpec is available — callers never have to remember to register.
from xyz_agent_context.memory import specs as _specs  # noqa: E402,F401

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
    "get_spec",
    "all_kinds",
    "passive_kinds",
    "MemoryEngine",
    "MemoryCoordinator",
    "MemoryHit",
]
