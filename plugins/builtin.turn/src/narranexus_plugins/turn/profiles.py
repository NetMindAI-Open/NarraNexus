"""
@file_name: profiles.py
@author: Bin Liang
@date: 2026-09-04
@description: The five builtin ``PipelineProfile``s — fast/voice/job/silent as named strategy selections instead of scattered flags.

Each profile names, per stage, the strategy registered in the
``turn.pipeline.<stage>`` registry; a stage not named uses ``default``.
``TurnProfile`` (schema) keeps carrying the per-turn knobs the Act stage
still reads (reasoning effort, prompt mode, framework override); the
profile decides WHICH strategy runs, the knobs tune HOW.

Each profile also carries the CONDITION under which the host picks it, as a
``when`` clause over the turn facts ``platform.turn.turn_when_context``
publishes (``silent`` / ``fast_mode`` / ``voice`` / ``narrative_strategy`` /
``source``), plus an ``order`` that breaks ties (lowest first). Those five
clauses ARE the if-chain ``resolve_profile`` used to hold: because they are
data on the profile, a plugin profile competes on equal terms — the whole
point of the ``turn.profiles`` slot. ``default`` deliberately carries no
``when``: it is the fallback the host returns when nothing matched, so a
clause that "always holds" would shadow every other profile.

Symbol names follow ``docs/API_POLICY.md`` §8: a many-arity slot is filled by
``PROFILES``, the same name ``templates/pipeline_profile`` scaffolds.
"""
from __future__ import annotations

from narranexus.contracts.agent.pipeline import PipelineProfile
from narranexus.contracts.agent.stages import Stage
from narranexus.kernel.plugins.registry import Contribution

DEFAULT = PipelineProfile(id="default")
# The old if-chain, as data. Orders mirror its order: silent short-circuited
# first, then a voice TurnProfile, then bm25_top1 / fast_mode, then job turns.
SILENT = PipelineProfile(id="silent", strategies={Stage.ACT: "silent"}, when="silent", order=10)
VOICE = PipelineProfile(
    id="voice",
    strategies={Stage.RECALL: "ephemeral"},
    narrative_persistence="ephemeral",
    when="voice",
    order=20,
)
FAST = PipelineProfile(
    id="fast",
    strategies={Stage.RECALL: "narrative_fast"},
    narrative_persistence="durable",
    when="narrative_strategy == 'bm25_top1' or fast_mode",
    order=30,
)
JOB = PipelineProfile(id="job", when="source == 'job'", order=40)

BUILTIN_PROFILES: dict[str, PipelineProfile] = {p.id: p for p in (DEFAULT, FAST, VOICE, JOB, SILENT)}

PROFILES = tuple(Contribution(pid, (lambda p=p: p)) for pid, p in BUILTIN_PROFILES.items())

__all__ = ["BUILTIN_PROFILES", "DEFAULT", "FAST", "JOB", "PROFILES", "SILENT", "VOICE"]
