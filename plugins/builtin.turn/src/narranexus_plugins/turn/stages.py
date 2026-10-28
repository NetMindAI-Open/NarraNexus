"""
@file_name: stages.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.turn — the default strategy per stage, as the contributions the manifest names (turn.pipeline.<stage>).

Symbol names follow ``docs/API_POLICY.md`` §8: a many-arity stage slot is
filled by ``<STAGE>_STRATEGIES``, which is what ``templates/stage_strategy``
scaffolds — so a third party who copies this package and one who runs
``narranexus plugin new --kinds stage_strategy`` end up with the same names.
"""
from __future__ import annotations

from narranexus.contracts.agent.stages import Stage
from narranexus.kernel.plugins.registry import Contribution
from narranexus_plugins.turn import act, assemble, commit, compose, ingress, recall, reflect

INGRESS_STRATEGIES = (Contribution("default", lambda: ingress.DefaultIngress()),)
RECALL_STRATEGIES = (
    Contribution("default", lambda: recall.NarrativeLlmRecall()),
    Contribution("narrative_fast", lambda: recall.NarrativeFastRecall()),
    Contribution("ephemeral", lambda: recall.EphemeralRecall()),
)
COMPOSE_STRATEGIES = (Contribution("default", lambda: compose.DefaultCompose()),)
ASSEMBLE_STRATEGIES = (Contribution("default", lambda: assemble.LayeredPromptAssemble()),)
ACT_STRATEGIES = (
    Contribution("default", lambda: act.AgentLoopAct()),
    Contribution("silent", lambda: act.SilentAct()),
)
COMMIT_STRATEGIES = (Contribution("default", lambda: commit.DefaultCommit()),)
REFLECT_STRATEGIES = (Contribution("default", lambda: reflect.BackgroundReflect()),)

STRATEGIES: dict[Stage, tuple[Contribution, ...]] = {
    Stage.INGRESS: INGRESS_STRATEGIES,
    Stage.RECALL: RECALL_STRATEGIES,
    Stage.COMPOSE: COMPOSE_STRATEGIES,
    Stage.ASSEMBLE: ASSEMBLE_STRATEGIES,
    Stage.ACT: ACT_STRATEGIES,
    Stage.COMMIT: COMMIT_STRATEGIES,
    Stage.REFLECT: REFLECT_STRATEGIES,
}

__all__ = ["ACT_STRATEGIES", "ASSEMBLE_STRATEGIES", "COMMIT_STRATEGIES", "COMPOSE_STRATEGIES", "INGRESS_STRATEGIES", "RECALL_STRATEGIES", "REFLECT_STRATEGIES", "STRATEGIES"]
