"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-07
@description: The stage-slot path grammar shared by the pipeline and its tests.

Nothing is declared or registered here. The seven ``turn.pipeline.<stage>``
slots — and the strategies filling them — are declared by ``builtin.turn``'s
manifest and created by the host boot (``load_builtins``). This module used to
carry a ``declare_stage_slots()`` helper that the pipeline's constructor called
on any registries object; it built a SECOND ``Slot`` for each of the same seven
paths with no ``kind``, and ``SlotTree.declare_all`` keeps whichever landed
first — so in any process that built a pipeline before boot, the seven slots
third parties are meant to extend reported ``api_version == 0`` and every
``api["stage_strategy"]`` compatibility check on a stage plugin passed
vacuously. A test that needs the tree calls ``load_builtins(regs, "backend")``,
exactly like the hosts.
"""
from __future__ import annotations

from narranexus.contracts.agent.stages import Stage

#: The plugin that declares the stage slots (its manifest is the definition).
OWNER = "builtin.turn"
STAGE_CONTRACT = "narranexus.contracts.agent.pipeline:StageStrategy"


def slot_path(stage: Stage) -> str:
    """The registry path a stage's strategies live under."""
    return f"turn.pipeline.{stage.value}"


__all__ = ["OWNER", "STAGE_CONTRACT", "slot_path"]
