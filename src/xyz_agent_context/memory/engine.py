"""
@file_name: engine.py
@author: NetMind.AI
@date: 2026-06-03
@description: MemoryEngine — the fixed algorithm every memory kind runs through.

This is the MECHANISM half of the design (§3, §5). Each public method is a
step of the universal memory lifecycle; per-kind behaviour comes entirely from
the MemoryKindSpec passed in / looked up. Improving a step here benefits every
kind at once — that is the whole point of the unification.

    write:  retain → (resolve = dedup + supersession) → persist → mark_dirty
    read:   recall (BM25+boosts) · grep (exact/regex)
    async:  consolidate (LLM 9-rules) · evict (lifecycle cap)

No embeddings anywhere.
"""
from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional, Sequence

from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.agent_framework.helper_sdk import get_helper_sdk
from xyz_agent_context.memory.record import MemoryRecord
from xyz_agent_context.memory.spec import MemoryKindSpec, get_spec
from xyz_agent_context.memory._memory_impl.repository import MemoryRepository
from xyz_agent_context.memory._memory_impl import dedup as _dedup
from xyz_agent_context.memory._memory_impl import retrieval as _retrieval
from xyz_agent_context.memory._memory_impl import consolidate as _consolidate
from xyz_agent_context.utils.timezone import utc_now

_QUEUE_TABLE = "memory_consolidation_queue"
# Per-turn recall/grep loads at most this many most-recent rows per kind before
# ranking, so a high-volume kind (10k-row event log) never scans the whole
# table in the agent loop. Recency is the prefilter; grep covers exact lookups.
_CANDIDATE_CAP = 300


class _DedupTieBreak(BaseModel):
    """LLM verdict when the deterministic funnel is ambiguous."""
    match_index: Optional[int] = Field(default=None, description="Index of the matching candidate, or null if none match.")


class MemoryEngine:
    """One engine per (agent, db). Repositories are created lazily per kind and
    cached, so a turn touching several kinds opens each table once."""

    def __init__(self, db_client: Any, agent_id: str, *, sdk: Optional[Any] = None):
        self._db = db_client
        self.agent_id = agent_id
        self._sdk = sdk or get_helper_sdk()
        self._repos: Dict[str, MemoryRepository] = {}

    def repo(self, kind: str) -> MemoryRepository:
        repo = self._repos.get(kind)
        if repo is None:
            repo = MemoryRepository(kind, self._db)
            self._repos[kind] = repo
        return repo

    # ── write ───────────────────────────────────────────────────────────────
    async def retain(self, record: MemoryRecord, *, spec: Optional[MemoryKindSpec] = None) -> MemoryRecord:
        """Persist one unit of memory, deduping/superseding per its kind spec,
        then mark its scope dirty for background consolidation."""
        spec = spec or get_spec(record.kind)
        record.agent_id = record.agent_id or self.agent_id
        persisted = await self._resolve(spec, record)
        if spec.consolidates:
            await self._mark_dirty(persisted.scope_type, persisted.scope_id, record.kind)
        return persisted

    async def _resolve(self, spec: MemoryKindSpec, incoming: MemoryRecord) -> MemoryRecord:
        repo = self.repo(incoming.kind)
        if spec.dedup_key is None:  # append-only kind (event/chat/bus)
            await repo.upsert(incoming)
            return incoming

        existing = await repo.query(
            agent_id=incoming.agent_id, scope_type=incoming.scope_type,
            scope_id=incoming.scope_id, live_only=True,
        )
        verdict = _dedup.funnel(spec.dedup_key(incoming), existing, key_of=spec.dedup_key)
        match = verdict.match
        if verdict.decision == "ambiguous":
            match = await self._llm_tiebreak(spec, incoming, verdict.candidates)

        if match is not None:
            merged = (spec.merge or self._default_merge)(match, incoming)
            await repo.upsert(merged)
            return merged
        await repo.upsert(incoming)
        return incoming

    @staticmethod
    def _default_merge(existing: MemoryRecord, incoming: MemoryRecord) -> MemoryRecord:
        """Generic union-merge: newer content wins (old kept in history), tags
        and provenance unioned, evidence incremented. Kinds with richer merge
        semantics (entity description append) override via spec.merge."""
        now = utc_now()
        if incoming.content_text and incoming.content_text != existing.content_text:
            existing.history.append({"text": existing.content_text, "changed_at": now.isoformat()})
            existing.content_text = incoming.content_text
        existing.tags = list(dict.fromkeys(existing.tags + incoming.tags))
        existing.source_ids = list(dict.fromkeys(existing.source_ids + incoming.source_ids))
        existing.attributes = {**existing.attributes, **incoming.attributes}
        existing.proof_count += max(1, incoming.proof_count)
        existing.updated_at = now
        return existing

    async def _llm_tiebreak(
        self, spec: MemoryKindSpec, incoming: MemoryRecord, candidates: Sequence[MemoryRecord]
    ) -> Optional[MemoryRecord]:
        """Ask the LLM to pick the true match among fuzzy candidates. Defaults
        to CREATE-NEW (returns None) on any failure — a false split is cheaper
        than a false merge."""
        if not spec.dedup_prompt or not candidates:
            return None
        listing = "\n".join(f"[{i}] {c.content_text}" for i, c in enumerate(candidates))
        try:
            result = await self._sdk.llm_function(
                instructions=spec.dedup_prompt,
                user_input=f"NEW:\n{incoming.content_text}\n\nCANDIDATES:\n{listing}",
                output_type=_DedupTieBreak,
                agent_id=self.agent_id,
                db=self._db,  # explicit — record even when no ambient cost ctx
            )
            idx = result.final_output.match_index
            return candidates[idx] if idx is not None and 0 <= idx < len(candidates) else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[memory.engine] dedup tie-break failed, creating new: {e}")
            return None

    # ── index (search-projection write; design §6) ───────────────────────────
    async def index(
        self, kind: str, source_id: str, text: str, *,
        scope_type: str = "agent", scope_id: str = "",
        subtype: Optional[str] = None, tags: Optional[Sequence[str]] = None,
        agent_id: Optional[str] = None,
    ) -> MemoryRecord:
        """Write/refresh ONE search-index record for a source record.

        This is the single, uniform entry point every PROJECTION kind
        (narrative / interaction / job / bus) uses to make its source data
        searchable. The index row holds the searchable ``text`` + a
        ``source_ref`` pointer back to the original — it is NOT the source of
        truth (that stays in the operational table); recall returns the pointer
        and the agent fetches the live original via the per-kind by-id tool.

        Idempotent: ``record_id`` is deterministic from (kind, source_id), so
        re-indexing the same source (e.g. a job whose status changed) just
        upserts the same row — safe to call from a per-turn hook.
        """
        rid = f"idx_{kind}_{hashlib.sha1(source_id.encode('utf-8')).hexdigest()[:20]}"
        rec = MemoryRecord(
            record_id=rid,
            agent_id=agent_id or self.agent_id,
            scope_type=scope_type,
            scope_id=scope_id or "",
            kind=kind,
            subtype=subtype,
            content_text=text or "",
            tags=list(tags or []),
            source_ref={"kind": kind, "id": source_id},
        )
        await self.repo(kind).upsert(rec)
        return rec

    # ── read ────────────────────────────────────────────────────────────────
    async def recall(
        self, kind: str, query: str, *,
        scope_type: Optional[str] = None, scope_id: Optional[str] = None,
        subtype: Optional[str] = None, valid_now: bool = True,
        limit: Optional[int] = None, token_budget: Optional[int] = None,
    ) -> List[MemoryRecord]:
        spec = get_spec(kind)
        repo = self.repo(kind)
        candidates = await repo.query(
            agent_id=self.agent_id, scope_type=scope_type, scope_id=scope_id,
            subtype=subtype, live_only=True, valid_now=valid_now,
            candidate_cap=_CANDIDATE_CAP,
        )
        w = spec.recall
        hits = _retrieval.rank_recall(
            candidates, query, limit=limit, token_budget=token_budget,
            w_recency=w.recency, w_proof=w.proof, w_salience=w.salience,
        )
        for r in hits:  # recency feedback
            await repo.touch(r.record_id)
        return hits

    async def grep(
        self, kind: str, pattern: str, *,
        scope_type: Optional[str] = None, scope_id: Optional[str] = None,
        regex: bool = False, limit: Optional[int] = None,
    ) -> List[MemoryRecord]:
        candidates = await self.repo(kind).query(
            agent_id=self.agent_id, scope_type=scope_type, scope_id=scope_id, live_only=True,
            candidate_cap=_CANDIDATE_CAP * 4,  # grep scans more (exact lookups reach deeper)
        )
        hits = _retrieval.grep_filter(candidates, pattern, regex=regex)
        return hits[:limit] if limit else hits

    # ── async background ─────────────────────────────────────────────────────
    async def consolidate(
        self, target_kind: str, *, scope_type: str, scope_id: str,
        new_facts: Sequence[MemoryRecord], existing: Sequence[MemoryRecord],
    ) -> int:
        spec = get_spec(target_kind)
        return await _consolidate.consolidate(
            self.repo(target_kind), agent_id=self.agent_id,
            scope_type=scope_type, scope_id=scope_id, kind=target_kind,
            new_facts=new_facts, existing=existing,
            prompt=spec.consolidate_prompt, sdk=self._sdk,
            db=self._db,  # explicit — record even when no ambient cost ctx
        )

    async def evict(self, kind: str, *, scope_type: str, scope_id: str) -> int:
        """Enforce the per-scope cap, tombstoning the lowest-value records
        (oldest, least salient, least-proven). No-op when cap is unset."""
        spec = get_spec(kind)
        cap = spec.max_records_per_scope
        if cap is None:
            return 0
        repo = self.repo(kind)
        records = await repo.query(agent_id=self.agent_id, scope_type=scope_type, scope_id=scope_id, live_only=True)
        if len(records) <= cap:
            return 0
        now = utc_now()
        records.sort(key=lambda r: (r.salience, r.proof_count, _retrieval.recency_boost(r, now)))
        evicted = 0
        for r in records[: len(records) - cap]:
            await repo.tombstone(r.record_id)
            evicted += 1
        return evicted

    # ── consolidation dirty queue (design §7.4) ─────────────────────────────
    async def _mark_dirty(self, scope_type: str, scope_id: str, kind: str) -> None:
        """Cheap, synchronous: bump the (scope, kind) dirty counter. The
        background worker drains it. Never does LLM work in the turn."""
        now = utc_now()
        key = {"agent_id": self.agent_id, "scope_type": scope_type, "scope_id": scope_id or "", "kind": kind}
        row = await self._db.get_one(_QUEUE_TABLE, key)
        if row:
            await self._db.update(_QUEUE_TABLE, key, {
                "pending_count": int(row.get("pending_count") or 0) + 1,
                "last_dirty_at": now, "status": "dirty", "updated_at": now,
            })
        else:
            await self._db.insert(_QUEUE_TABLE, {**key, "pending_count": 1, "last_dirty_at": now, "status": "dirty"})
