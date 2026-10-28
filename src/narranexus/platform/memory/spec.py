"""
@file_name: spec.py
@author: NetMind.AI
@date: 2026-06-03
@description: MemoryKindSpec — the per-kind POLICY object + its registry.

This is the other half of the "mechanism vs policy" split (design §3). The
MemoryEngine owns the fixed ALGORITHM (extract → resolve → persist →
consolidate → evict → recall → grep); a MemoryKindSpec declares the per-kind
PARAMETERS (how to key for dedup, which prompts to use, how to rank/render,
when to evict). Improving a mechanism = editing one Engine method (all kinds
benefit). Tuning a kind = editing one Spec (only that kind changes).

A Spec is pure data + a few small pure callables — never the algorithm itself.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution, Registry
from narranexus.platform.memory.record import MemoryRecord, SCOPE_AGENT

# Policy callables (all pure; the Engine supplies context, the Spec supplies how)
RenderFn = Callable[[List[MemoryRecord]], str]
KeyFn = Callable[[MemoryRecord], str]
# Merge an incoming duplicate into the existing record, returning the existing
# record mutated. Default (engine-supplied) is a generic union-merge; entity
# overrides it to append its description instead of replacing.
MergeFn = Callable[[MemoryRecord, MemoryRecord], MemoryRecord]


@dataclass(frozen=True)
class RecallWeights:
    """Per-kind ranking knobs for `recall` (mechanism reads these)."""
    recency: float = 0.5
    proof: float = 0.3
    salience: float = 0.2


@dataclass(frozen=True)
class MemoryKindSpec:
    """Declarative policy for one memory kind."""

    kind: str
    default_scope: str = SCOPE_AGENT
    subtypes: Tuple[str, ...] = ()

    # ── write policy ───────────────────────────────────────────────────────
    # Dedup: key extractor (None ⇒ kind is append-only, no dedup, e.g. event/chat).
    dedup_key: Optional[KeyFn] = None
    # Merge policy for a confirmed duplicate (None ⇒ Engine's generic union-merge).
    merge: Optional[MergeFn] = None
    # LLM prompts (None ⇒ that step is skipped for this kind):
    dedup_prompt: Optional[str] = None          # tie-break when funnel is ambiguous
    contradiction_prompt: Optional[str] = None  # detect bi-temporal supersession
    extract_prompt: Optional[str] = None        # turn an event into raw units (None ⇒ caller builds records)
    consolidate_prompt: Optional[str] = None     # distil raw units → observations/summary

    # ── consolidation trigger (design §7.4; per-kind override of defaults) ──
    consolidate_threshold: int = 4
    consolidates: bool = False  # convenience flag the worker reads

    # ── read policy ────────────────────────────────────────────────────────
    recall: RecallWeights = field(default_factory=RecallWeights)
    # True ⇒ this kind participates in the PASSIVE per-turn injection (distilled
    # knowledge: observation/entity/narrative). False ⇒ searchable only via the
    # remember/grep TOOLS (raw/voluminous: interaction/job/bus). Both surfaces
    # call coordinator.remember with different kind lists. (design §4)
    passive: bool = False
    render: Optional[RenderFn] = None

    # ── lifecycle ──────────────────────────────────────────────────────────
    max_records_per_scope: Optional[int] = None  # None ⇒ unbounded (eviction off)


# ── registry ────────────────────────────────────────────────────────────────
# Kernel registry for slot ``agent.capabilities.memory_kinds`` (plugin platform,
# batch 0). Specs are registered eagerly (the factory returns the spec itself);
# ``replace=True`` keeps re-import idempotent.
MEMORY_KINDS_SLOT = "agent.capabilities.memory_kinds"
_CONTRIBUTIONS: Dict[str, Contribution[MemoryKindSpec]] = {}


def memory_kind_registry(registries: Any = None) -> Registry[MemoryKindSpec]:
    """The registry for slot ``agent.capabilities.memory_kinds``, resolved at call time; populated by the host boot from the builtin.memory_kinds manifest."""
    return (registries or KERNEL_REGISTRIES).registry_for(MEMORY_KINDS_SLOT)


def declare_spec(spec: MemoryKindSpec) -> Contribution[MemoryKindSpec]:
    """Make a kind's spec a Contribution a manifest can name (no registry write: the host boot registers what the manifests name)."""
    contribution: Contribution[MemoryKindSpec] = Contribution(spec.kind, lambda: spec)
    _CONTRIBUTIONS[spec.kind] = contribution
    return contribution


def register_spec(spec: MemoryKindSpec, *, owner: str, registries: Any = None, replace: bool = False) -> MemoryKindSpec:
    """Explicit registration (tests, an embedding host)."""
    contribution = _CONTRIBUTIONS.get(spec.kind) or declare_spec(spec)
    memory_kind_registry(registries).register_contribution(contribution, owner=owner, replace=replace)
    return spec


def contribution_for(kind: str) -> Contribution[MemoryKindSpec]:
    """The plugin-platform contribution behind a declared kind (for builtin manifests)."""
    return _CONTRIBUTIONS[kind]


def get_spec(kind: str) -> MemoryKindSpec:
    spec = memory_kind_registry().try_get(kind)
    if spec is None:
        raise KeyError(f"No MemoryKindSpec registered for kind={kind!r}")
    return spec


def all_kinds() -> List[str]:
    return list(memory_kind_registry().names())


def passive_kinds() -> List[str]:
    """Kinds eligible for the passive per-turn injection (distilled knowledge)."""
    return [e.name for e in memory_kind_registry().entries() if e.factory().passive]
