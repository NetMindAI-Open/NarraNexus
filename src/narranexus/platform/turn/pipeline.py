"""
@file_name: pipeline.py
@author: Bin Liang
@date: 2026-09-07
@description: The platform's turn seams: which PROFILE this turn runs under, and which bound implementation runs it.

Selection, not implementation. The pipeline that actually walks the seven
stages is a plugin contribution (``builtin.turn`` →
``narranexus_plugins.turn.pipeline:CONTRIBUTION``); it used to live here, which
made the ``turn.pipeline`` slot the one extension point no third party could
fill — the platform held both the slot and its only possible filler, and a
distribution that "excluded" builtin.turn still had ``TurnPipeline`` importable
in the engine wheel. The platform now reaches it through the binding
(``bound_entry``), so replacing the whole turn runtime really replaces it.

Profile selection is the platform's job because it is about the TURN's facts,
not about any one profile: ``resolve_profile`` builds a small fact bag and asks
every registered profile's own ``when`` clause whether it applies. It used to
be an if-chain naming the five builtin profile ids, so a plugin-defined profile
could never be selected unless the caller named it explicitly.
"""
from __future__ import annotations

from typing import Any

from narranexus.contracts import UnknownEntry
from narranexus.contracts.agent.pipeline import PipelineProfile, evaluate_when
from narranexus.kernel.plugins.bound import bound_entry
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES, Registries

PROFILES_SLOT = "turn.profiles"
PIPELINE_SLOT = "turn.pipeline"


def turn_pipeline_for(registries: Registries | None = None) -> Any:
    """The pipeline class bound to ``turn.pipeline`` (call it with the
    registries to build one).

    Fails loud through ``bound_entry`` when the bound plugin contributes
    nothing there: a turn with no pipeline must not silently fall back to
    whichever plugin registered first.
    """
    regs = registries or KERNEL_REGISTRIES
    return bound_entry(regs, PIPELINE_SLOT).factory()


def turn_when_context(
    *,
    fast_mode: bool,
    silent: bool,
    turn_profile: Any,
    working_source: Any,
) -> dict[str, Any]:
    """The facts a profile's ``when`` clause may read for THIS turn.

    A closed, flat vocabulary — the contract between the host and every
    profile author, builtin or third-party:

    * ``silent``            — the turn ingests without answering
    * ``fast_mode``         — the caller asked for the fast path
    * ``voice``             — the turn profile is a voice one
    * ``narrative_strategy``— the turn profile's recall strategy (``""`` when
      the turn carries no profile)
    * ``source``            — the working source (``chat``, ``job``,
      ``discord``, … — a channel plugin's own name appears here for free)

    Adding a fact here is additive: an unknown key evaluates falsy, so an old
    profile keeps working.
    """
    name = str(getattr(turn_profile, "name", "") or "") if turn_profile is not None else ""
    return {
        "silent": bool(silent),
        "fast_mode": bool(fast_mode),
        "voice": "voice" in name,
        "narrative_strategy": (
            str(getattr(turn_profile, "narrative_strategy", "") or "") if turn_profile is not None else ""
        ),
        "source": str(getattr(working_source, "value", working_source) or ""),
    }


def resolve_profile(
    *,
    fast_mode: bool,
    silent: bool,
    turn_profile: Any,
    working_source: Any,
    registries: Registries | None = None,
    explicit: str | None = None,
) -> PipelineProfile:
    """Profile selection: explicit id > best matching ``when`` (lowest ``order``) > ``default``.

    ``explicit`` is the TURN layer of the binding order (a caller naming a
    registered profile, e.g. a plugin-defined ``research``); it must exist in
    the ``turn.profiles`` registry, else ``UnknownEntry``.

    The middle step is the whole point of A2-7: every registered profile is
    asked, so ``PipelineProfile(id="acme_discord", when="source == 'discord'")``
    contributed by a plugin wins a discord turn with no platform edit. Ties go
    to the lowest ``order``, then to registration order — deterministic, and
    explainable by printing the profiles' clauses.
    """
    regs = registries or KERNEL_REGISTRIES
    if explicit:
        return _profile(explicit, regs)
    ctx = turn_when_context(
        fast_mode=fast_mode, silent=silent, turn_profile=turn_profile, working_source=working_source
    )
    best: PipelineProfile | None = None
    for profile in _registered_profiles(regs):
        if profile.when and evaluate_when(profile.when, ctx) and (best is None or profile.order < best.order):
            best = profile
    return best if best is not None else _profile("default", regs)


def _registered_profiles(regs: Registries) -> list[PipelineProfile]:
    if PROFILES_SLOT not in regs.slots:
        return []
    registry = regs.registry_for(PROFILES_SLOT)
    return [registry.get(name) for name in registry.names()]


def _profile(profile_id: str, regs: Registries) -> PipelineProfile:
    if PROFILES_SLOT in regs.slots:
        registry = regs.registry_for(PROFILES_SLOT)
        if profile_id in registry:
            return registry.get(profile_id)
    raise UnknownEntry(f"turn profile {profile_id!r} is not registered (turn.profiles)")


__all__ = [
    "PIPELINE_SLOT",
    "PROFILES_SLOT",
    "resolve_profile",
    "turn_pipeline_for",
    "turn_when_context",
]
