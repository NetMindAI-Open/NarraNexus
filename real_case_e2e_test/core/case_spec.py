"""
@file_name: case_spec.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: CaseSpec + TalkLine — the only schema a case author has to learn

Two dataclasses define the entire surface of a test case:

- ``CaseSpec`` is module-level metadata (id, pillar, linked bugs, tags,
  timeouts). The runner reads it to filter, group, and report.
- ``TalkLine`` is a single utterance in the pre-scripted dialogue.
  Optional ``expect_contains`` / ``expect_not_contains`` let a case
  encode lightweight string-level expectations without dragging the
  semantic phase in.

Both are frozen dataclasses — cases declare them at module scope so the
discovery walker can introspect without executing case code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


Severity = Literal["P0", "P1", "P2", "P3"]
Role = Literal["user", "system"]


@dataclass(frozen=True)
class TalkLine:
    """One scripted utterance the driver will send to an agent.

    The driver is deterministic; ``expect_contains`` and
    ``expect_not_contains`` are pure string checks recorded by the
    programmatic phase. Anything richer goes into the semantic phase
    via the SPEC's ``semantic_intent`` field.
    """
    role: Role
    content: str
    expect_contains: list[str] = field(default_factory=list)
    expect_not_contains: list[str] = field(default_factory=list)
    # Hard ceiling per turn. When the WS produces no completion within
    # this window we abort the turn and continue to the next line so a
    # hang in one turn does not pin the whole case.
    turn_timeout_seconds: Optional[int] = None


@dataclass(frozen=True)
class CaseSpec:
    """Everything the runner needs to schedule, report on, and audit a case."""

    # Unique, stable across runs. Use ``<pillar>__<NN>_<name>``.
    # Used as the file-system key in transcripts / programmatic /
    # semantic, so changing it after a case has history will break the
    # trend story for that case.
    case_id: str

    # Top-level grouping. Matches the folder under ``cases/``. Pillars
    # are scheduled as separate groups by the runner so a 429 in one
    # pillar does not bleed into another.
    pillar: str

    # One-line, human readable. Shows up at the top of every report.
    description: str

    # Lark Base bug IDs this case is meant to detect regressions for.
    # The semantic phase quotes these by id in its verdict.
    linked_bugs: list[str] = field(default_factory=list)

    # Drives sort + filter; the report groups failures by severity.
    severity: Severity = "P2"

    # Free-form labels. Standard tags: "needs-llm" (case calls the
    # configured LLM), "needs-lark-bot", "single-turn", "multi-turn",
    # "expected-fail" (known red until referenced bug is fixed).
    tags: list[str] = field(default_factory=list)

    # Default timeout per turn, used when a TalkLine does not override.
    turn_timeout_seconds: int = 180

    # Optional hint for the semantic phase: what should pass look like.
    # Free-form sentence; the prompt template surfaces it to Claude
    # Code so its verdict references the intent, not just the
    # transcript.
    semantic_intent: str = ""
