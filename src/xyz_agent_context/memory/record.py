"""
@file_name: record.py
@author: NetMind.AI
@date: 2026-06-03
@description: MemoryRecord — the single, unified shape every memory kind shares.

This is the heart of the memory unification (design doc §4). Narrative
summaries, events, chat messages, social entities, bus messages, job memos and
the new general observations are ALL stored as a MemoryRecord — same columns,
different `kind`. That uniformity is what lets one generic MemoryEngine /
MemoryRepository serve every kind, so improving a mechanism once benefits all
kinds (design principle §3: mechanism vs policy).

A record carries:
  - identity/scope: record_id, agent_id, (scope_type, scope_id), kind, subtype
  - content_text:   the unified natural-language surface — the BM25 + grep
                    target, and the text shown to the LLM. EVERY kind fills it.
  - attributes:     kind-specific structured payload (JSON)
  - bi-temporal:    valid_at/invalid_at (reality axis, LLM-extracted) +
                    created_at/expired_at (system axis, code-written). A
                    contradicted record is tombstoned (invalid_at + expired_at),
                    never deleted — full history is retained.
  - provenance:     source_ids (which events/records produced this) + proof_count
  - lifecycle:      history (evolution snapshots), salience, last_used_at

No embeddings anywhere — retrieval is BM25 + grep + structured filters.
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from xyz_agent_context.utils.timezone import utc_now

# Scope types — who/what a memory belongs to.
SCOPE_AGENT = "agent"
SCOPE_USER = "user"
SCOPE_NARRATIVE = "narrative"
SCOPE_INSTANCE = "instance"
SCOPE_GLOBAL = "global"
VALID_SCOPES = frozenset({SCOPE_AGENT, SCOPE_USER, SCOPE_NARRATIVE, SCOPE_INSTANCE, SCOPE_GLOBAL})

ID_PREFIX = "mem_"

# JSON-typed columns (stored as text, (de)serialized at the row boundary).
_JSON_FIELDS = ("attributes", "tags", "source_ids", "history", "source_ref")
_DT_FIELDS = ("valid_at", "invalid_at", "expired_at", "last_used_at", "created_at", "updated_at")


def new_record_id() -> str:
    """`mem_` + 8 random hex chars — matches the project ID convention."""
    return f"{ID_PREFIX}{secrets.token_hex(4)}"


def _parse_dt(value: Any) -> Optional[datetime]:
    """Tolerant datetime parse: SQLite returns ISO strings, MySQL may return
    datetime objects. Returns None on empty/unparseable input rather than
    raising — a malformed timestamp must not crash a memory read."""
    if value is None or value == "":
        return None
    dt: Optional[datetime] = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            # Tolerate a space separator ("YYYY-MM-DD HH:MM:SS") that some
            # legacy rows used instead of ISO 'T' (see MEMORY.md timestamp bug).
            try:
                dt = datetime.fromisoformat(value.replace(" ", "T", 1))
            except ValueError:
                return None
    if dt is None:
        return None
    # Normalize to timezone-aware UTC. The DB stores UTC (utc_now() writes it),
    # but MySQL DATETIME comes back as a NAIVE datetime object and offset-less
    # ISO strings parse naive — and `utc_now() - naive` raises "can't subtract
    # offset-naive and offset-aware datetimes" (this crashed the memory
    # consolidation worker on the MySQL/cloud backend every pass). Interpret a
    # naive timestamp as UTC so all arithmetic against utc_now() is safe.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _parse_json(value: Any, default: Any) -> Any:
    """Tolerant JSON parse for a row column. Accepts already-decoded
    dict/list (some backends auto-decode JSON columns)."""
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return default
    return default


class MemoryRecord(BaseModel):
    """One unit of agent memory, uniform across all kinds."""

    record_id: str = Field(default_factory=new_record_id)
    agent_id: str
    scope_type: str = SCOPE_AGENT
    scope_id: str = ""
    kind: str = ""
    subtype: Optional[str] = None

    content_text: str = ""
    attributes: Dict[str, Any] = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list)

    # bi-temporal (design §9.2)
    valid_at: Optional[datetime] = None
    invalid_at: Optional[datetime] = None
    expired_at: Optional[datetime] = None

    # provenance + confidence
    source_ids: List[str] = Field(default_factory=list)
    # Pointer back to the ORIGINAL record this row indexes (search-projection
    # kinds: narrative/interaction/job/bus). {"kind": <source kind>, "id": <id>}.
    # None ⇒ self-contained kind (observation/entity): the row IS the source.
    source_ref: Optional[Dict[str, str]] = None
    proof_count: int = 0
    history: List[Dict[str, Any]] = Field(default_factory=list)

    # lifecycle
    salience: float = 0.0
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # ── bi-temporal helpers ────────────────────────────────────────────────
    @property
    def is_live(self) -> bool:
        """A record the system still considers current (not tombstoned)."""
        return self.expired_at is None

    def is_valid_at(self, when: datetime) -> bool:
        """True if the fact was true-in-reality at `when` (valid-time window)."""
        if self.valid_at is not None and when < self.valid_at:
            return False
        if self.invalid_at is not None and when >= self.invalid_at:
            return False
        return True

    @property
    def is_currently_true(self) -> bool:
        """Live AND valid right now."""
        return self.is_live and self.is_valid_at(utc_now())

    # ── row (de)serialization ──────────────────────────────────────────────
    def to_row(self) -> Dict[str, Any]:
        """Convert to a DB row dict. The AsyncDatabaseClient serializes
        dict/list → JSON and datetime → ISO automatically, so we pass native
        Python values. `created_at` is emitted only when explicitly set: a
        fresh record (None) lets the column DEFAULT fill it on insert and is
        therefore never rewritten by an update; migration sets it to preserve
        the original timestamp. `updated_at` is always stamped now."""
        row: Dict[str, Any] = {
            "record_id": self.record_id,
            "agent_id": self.agent_id,
            "scope_type": self.scope_type,
            "scope_id": self.scope_id or "",
            "kind": self.kind,
            "subtype": self.subtype,
            "content_text": self.content_text,
            "attributes": self.attributes,
            "tags": self.tags,
            "valid_at": self.valid_at,
            "invalid_at": self.invalid_at,
            "expired_at": self.expired_at,
            "source_ids": self.source_ids,
            "source_ref": self.source_ref,
            "proof_count": self.proof_count,
            "history": self.history,
            "salience": self.salience,
            "last_used_at": self.last_used_at,
            "updated_at": utc_now(),
        }
        if self.created_at is not None:
            row["created_at"] = self.created_at
        return row

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "MemoryRecord":
        """Build a record from a raw DB row (JSON columns are strings here)."""
        data: Dict[str, Any] = {
            "record_id": row["record_id"],
            "agent_id": row["agent_id"],
            "scope_type": row.get("scope_type") or SCOPE_AGENT,
            "scope_id": row.get("scope_id") or "",
            "kind": row.get("kind") or "",
            "subtype": row.get("subtype"),
            "content_text": row.get("content_text") or "",
            "proof_count": int(row.get("proof_count") or 0),
            "salience": float(row.get("salience") or 0.0),
        }
        data["attributes"] = _parse_json(row.get("attributes"), {})
        data["tags"] = _parse_json(row.get("tags"), [])
        data["source_ids"] = _parse_json(row.get("source_ids"), [])
        data["source_ref"] = _parse_json(row.get("source_ref"), None)
        data["history"] = _parse_json(row.get("history"), [])
        for f in _DT_FIELDS:
            data[f] = _parse_dt(row.get(f))
        return cls(**data)
