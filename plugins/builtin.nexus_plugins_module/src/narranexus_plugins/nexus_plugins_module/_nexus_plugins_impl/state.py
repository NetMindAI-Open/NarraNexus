"""
@file_name: state.py
@author: Bin Liang
@date: 2026-09-03
@description: Persistent state of the self-extension flow: proposals (approval cards), budgets, and the immutable audit timeline.

All of it lives in the plugin home next to ``registry.json``:
``.proposals.json`` (pending/decided approval requests, written atomically
with a file lock), ``.budgets.json`` (per-agent budget/cooldown/rollback
counters) and ``.audit.jsonl`` (append-only: who/when/why/diff hash/test
report). The audit file is never rewritten — that is the "what did my
instance change" timeline (spec §11.5).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from narranexus.kernel.plugins.lifecycle import _atomic_write_json, _locked
from narranexus.kernel.plugins.paths import plugin_home

from .guards import APPROVAL_TIMEOUT_S, Budget

Decision = Literal["pending", "approved", "rejected", "expired"]


def _path(name: str) -> Path:
    return plugin_home() / name


@dataclass
class Proposal:
    id: str
    plugin_id: str
    agent_id: str
    user_id: str
    action: str  # activate | install | upgrade | deactivate
    scope: str
    summary: str
    permissions: dict[str, Any]
    test_report: dict[str, Any]
    diff_hash: str
    created_at: float
    decision: Decision = "pending"
    decided_at: float | None = None
    decided_by: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def expired(self, now: float | None = None) -> bool:
        return self.decision == "pending" and ((time.time() if now is None else now) - self.created_at) > APPROVAL_TIMEOUT_S


class ProposalStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _path(".proposals.json")

    def _read(self) -> list[Proposal]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return []
        return [Proposal(**p) for p in raw]

    def _write(self, items: list[Proposal]) -> None:
        _atomic_write_json(self.path, [asdict(p) for p in items])  # type: ignore[arg-type]

    def create(self, **kw: Any) -> Proposal:
        p = Proposal(id=f"prop_{uuid.uuid4().hex[:8]}", created_at=time.time(), **kw)
        with _locked(self.path):
            items = self._read()
            items.append(p)
            self._write(items)
        return p

    def list(self, *, agent_id: str | None = None, pending_only: bool = False) -> list[Proposal]:
        with _locked(self.path):
            items = self._read()
            changed = False
            for p in items:
                if p.expired():
                    p.decision = "expired"
                    p.decided_at = time.time()
                    changed = True
            if changed:
                self._write(items)
        out = [p for p in items if agent_id is None or p.agent_id == agent_id]
        return [p for p in out if not pending_only or p.decision == "pending"]

    def get(self, proposal_id: str) -> Proposal | None:
        return next((p for p in self.list() if p.id == proposal_id), None)

    def decide(self, proposal_id: str, decision: Literal["approved", "rejected"], *, by: str) -> Proposal:
        with _locked(self.path):
            items = self._read()
            for p in items:
                if p.id == proposal_id:
                    if p.expired():
                        p.decision = "expired"
                        self._write(items)
                        raise ValueError(f"proposal {proposal_id} expired after {APPROVAL_TIMEOUT_S // 60} minutes (fail-closed)")
                    if p.decision != "pending":
                        raise ValueError(f"proposal {proposal_id} already {p.decision}")
                    p.decision = decision
                    p.decided_at = time.time()
                    p.decided_by = by
                    self._write(items)
                    return p
        raise KeyError(proposal_id)


class BudgetStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _path(".budgets.json")

    def _read(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def get(self, agent_id: str) -> Budget:
        raw = self._read().get(agent_id, {})
        return Budget(events=list(raw.get("events", [])), consecutive_rollbacks=int(raw.get("consecutive_rollbacks", 0)), manual_required=bool(raw.get("manual_required", False)))

    def put(self, agent_id: str, budget: Budget) -> None:
        with _locked(self.path):
            data = self._read()
            data[agent_id] = asdict(budget)
            _atomic_write_json(self.path, data)

    def reset(self, agent_id: str) -> None:
        self.put(agent_id, Budget(events=[]))


class Audit:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or _path(".audit.jsonl")

    def record(self, *, agent_id: str, user_id: str, plugin_id: str, action: str, why: str = "", diff_hash: str = "", report: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
        row = {"at": time.time(), "agent_id": agent_id, "user_id": user_id, "plugin_id": plugin_id, "action": action, "why": why, "diff_hash": diff_hash, "report": report or {}, **extra}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return row

    def timeline(self, *, plugin_id: str | None = None, agent_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if plugin_id and row.get("plugin_id") != plugin_id:
                continue
            if agent_id and row.get("agent_id") != agent_id:
                continue
            rows.append(row)
        return rows[-limit:]


def summary_for_agent(agent_id: str) -> str:
    """One line for the instruction block: registered plugins of this agent + pending proposals."""
    try:
        from narranexus.kernel.plugins.lifecycle import RegistryStore
        from narranexus.kernel.plugins.paths import registry_path

        reg = RegistryStore(path=registry_path()).read() if registry_path().exists() else None
        mine = [pid for pid, r in (reg.plugins.items() if reg else []) if r.scope in ("global", f"agent:{agent_id}")]
        pending = len(ProposalStore().list(agent_id=agent_id, pending_only=True))
        return f"{len(mine)} plugin(s) visible to this agent ({', '.join(sorted(mine)) or 'none'}); {pending} pending approval."
    except Exception as exc:  # noqa: BLE001 — instructions must never fail a turn
        return f"(plugin state unavailable: {type(exc).__name__})"


__all__ = ["Audit", "BudgetStore", "Decision", "Proposal", "ProposalStore", "summary_for_agent"]
