"""
@file_name: transcript.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Per-case structured record of everything that happened

A Transcript is a write-once journal — append turns as they finish,
serialize to JSON when the case ends. Everyone downstream (programmatic,
semantic, report) reads the same JSON.

We deliberately do NOT materialize the joined backend log slice into the
Transcript itself. log_grep produces a parallel ``backend_log.txt`` next
to the transcript; keeping logs out of the JSON keeps the JSON small
enough to ship to Claude Code in one prompt without exceeding context.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from .case_spec import CaseSpec
from .ws_client import WSTurn


@dataclass
class CaseEnv:
    """Snapshot of the environment the case ran against."""
    narranexus_commit: Optional[str] = None
    base_url: str = ""
    ws_url: str = ""
    run_ts: str = ""


@dataclass
class TurnRecord:
    """The transcript view of one WS turn — drops bulky deltas for
    storage but keeps enough to reconstruct semantics."""
    turn_index: int
    role: str
    input_content: str
    expect_contains: list[str]
    expect_not_contains: list[str]
    run_id: Optional[str]
    started_at: float
    ended_at: Optional[float]
    duration_seconds: Optional[float]
    completed: bool
    timed_out: bool
    transport_error: Optional[str]
    final_reply: str
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_ws_turn(
        cls,
        turn_index: int,
        role: str,
        expect_contains: list[str],
        expect_not_contains: list[str],
        ws_turn: WSTurn,
    ) -> "TurnRecord":
        return cls(
            turn_index=turn_index,
            role=role,
            input_content=ws_turn.input_content,
            expect_contains=list(expect_contains),
            expect_not_contains=list(expect_not_contains),
            run_id=ws_turn.run_id,
            started_at=ws_turn.started_at,
            ended_at=ws_turn.ended_at,
            duration_seconds=ws_turn.duration_seconds,
            completed=ws_turn.completed,
            timed_out=ws_turn.timed_out,
            transport_error=ws_turn.transport_error,
            final_reply=ws_turn.final_reply,
            events=list(ws_turn.events),
        )


@dataclass
class Transcript:
    """Full record for one case run."""
    case_id: str
    pillar: str
    description: str
    linked_bugs: list[str]
    severity: str
    tags: list[str]
    semantic_intent: str
    env: CaseEnv
    user_id: Optional[str] = None
    agent_ids: list[str] = field(default_factory=list)
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    turns: list[TurnRecord] = field(default_factory=list)
    driver_error: Optional[str] = None
    # Cleanup failures are stored separately so they do not shadow the
    # binary verdict the case body actually produced. A case that ran
    # green but left orphan resources is still green on the gate; the
    # cleanup failure shows up in the manifest's top-level
    # `cleanup_failures` and in the per-case row, not as a case fail.
    cleanup_failures: list[str] = field(default_factory=list)

    @classmethod
    def from_spec(cls, spec: CaseSpec, env: CaseEnv) -> "Transcript":
        return cls(
            case_id=spec.case_id,
            pillar=spec.pillar,
            description=spec.description,
            linked_bugs=list(spec.linked_bugs),
            severity=spec.severity,
            tags=list(spec.tags),
            semantic_intent=spec.semantic_intent,
            env=env,
        )

    def write(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))
