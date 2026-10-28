"""
@file_name: narrative_routing_audit_repository.py
@date: 2026-08-07
@description: Persist the narrative-routing decision trail (E1).

Why this exists
===============
Narrative selection had no denominator. `selection_method`, the gate's score
evidence, and both LLM tiers' reasoning went to a ProgressMessage and loguru
and nowhere else — and docker logs rotate (incident lesson #5). So no question
of the form "how often does the continuity tier lock a thread it shouldn't?"
could be answered at all, and no routing change could be shown to have helped.

The non-obvious requirement
===========================
Storing IDs and scores is NOT enough to replay a decision. ``bm25_rank``
computes IDF and avgdl over the candidate set it is handed, so a candidate's
score depends on every other document in the pool; and the scored text
(``name`` + ``current_summary`` + ``description`` + ``topic_keywords``) is
rewritten wholesale by the async LLM updater on almost every turn with no
history retained. Re-reading `narratives` later therefore reconstructs a pool
that never existed, and replaying against it yields confident, wrong numbers.

So the audit stores the whole pool, each entry pointing at a content-addressed
snapshot of the exact text that was scored. Dedup makes this cheap: between
consecutive turns usually only the main narrative's summary moves, so a
100-candidate pool costs ~1 new snapshot row per turn.
``tests/narrative/test_routing_audit.py`` pins the replay property.

**Best-effort writes** — ``record`` NEVER raises into the caller. The observer
must not break the observed; losing an audit row beats failing a user's turn.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List, Optional, Set

from loguru import logger

from narranexus.platform.narrative.models import RoutingAudit

AUDIT_TABLE = "narrative_routing_audit"
SNAPSHOT_TABLE = "narrative_text_snapshots"


def text_hash(text: str) -> str:
    """Content address for a narrative's scored text.

    sha256 over UTF-8, not a shorter digest: these keys are the join between
    an audit row and the text it must reproduce exactly, and a collision
    silently corrupts a replay rather than failing it.
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


class NarrativeRoutingAuditRepository:
    """Append-only routing decision log plus its content-addressed text store."""

    def __init__(self, db_client):
        # Untyped on purpose, mirroring ServiceAuditRepository: the async DB
        # client is injected and importing its type here would only add a
        # load-order coupling for no benefit.
        self._db = db_client

    # ── write ───────────────────────────────────────────────────────────
    async def record(
        self,
        audit: RoutingAudit,
        snapshots: Optional[Dict[str, str]] = None,
    ) -> None:
        """Persist one routing decision. Never raises into the caller.

        ``snapshots`` maps text_hash -> scored text for every candidate in
        ``audit.candidates``. Snapshots are written FIRST so an audit row is
        never left pointing at text that does not exist.
        """
        try:
            if snapshots:
                await self._store_snapshots(snapshots)
            await self._db.insert(AUDIT_TABLE, self._to_row(audit))
        except Exception as e:  # noqa: BLE001 — audit writes are advisory
            logger.warning(
                f"[narrative.audit] write failed (agent={audit.agent_id}): "
                f"{type(e).__name__}: {e} (row dropped; audit is advisory)"
            )

    @staticmethod
    def _to_row(audit: RoutingAudit) -> Dict[str, Any]:
        return {
            "agent_id": audit.agent_id,
            "user_id": audit.user_id,
            "query_text": audit.query_text,
            "trigger": audit.trigger,
            "is_user_chat": int(audit.is_user_chat),
            "continuity_ran": int(audit.continuity_ran),
            "continuity_is_continuous": (
                None if audit.continuity_is_continuous is None
                else int(audit.continuity_is_continuous)
            ),
            "continuity_confidence": audit.continuity_confidence,
            "continuity_reason": audit.continuity_reason,
            "candidates_json": json.dumps(
                [c.model_dump() for c in audit.candidates], ensure_ascii=False
            ),
            "gate_short_circuit": (
                None if audit.gate_short_circuit is None else int(audit.gate_short_circuit)
            ),
            "gate_reason": audit.gate_reason,
            "gate_top1_raw": audit.gate_top1_raw,
            "gate_top2_raw": audit.gate_top2_raw,
            "gate_margin": audit.gate_margin,
            "bypass_score_gate": (
                None if audit.bypass_score_gate is None
                else int(audit.bypass_score_gate)
            ),
            "bypass_reason": audit.bypass_reason,
            "pool_is_shadow": int(audit.pool_is_shadow),
            # Merged routing. int() on the two booleans, pass-through on the
            # rest: None must survive as NULL — "this row was decided on the
            # two-call path" and "the merged call cost 0ms" are different facts.
            "merged_call": int(audit.merged_call),
            "merged_verdict": audit.merged_verdict,
            "merged_ms": audit.merged_ms,
            "merged_input_chars": audit.merged_input_chars,
            "merged_truncated": audit.merged_truncated,
            "anchor_bm25_rank": audit.anchor_bm25_rank,
            "anchor_raw_score": audit.anchor_raw_score,
            "anchor_in_menu": (
                None if audit.anchor_in_menu is None else int(audit.anchor_in_menu)
            ),
            "judge_ran": int(audit.judge_ran),
            "judge_category": audit.judge_category,
            "judge_matched_id": audit.judge_matched_id,
            "judge_reason": audit.judge_reason,
            "selection_method": audit.selection_method,
            "retrieval_method": audit.retrieval_method,
            "chosen_narrative_id": audit.chosen_narrative_id,
            "is_new": int(audit.is_new),
            # Passed through untouched, NULL included: None means "this tier
            # did not run", and coercing it to 0 would make a skipped judge
            # look like an instant one.
            "continuity_ms": audit.continuity_ms,
            "retrieve_ms": audit.retrieve_ms,
            "keyword_ms": audit.keyword_ms,
            "judge_ms": audit.judge_ms,
        }

    async def _store_snapshots(self, snapshots: Dict[str, str]) -> None:
        """Insert only the texts we have not seen before.

        One SELECT for the whole batch, then inserts for the misses — a pool
        that did not change between turns costs a single query and no writes.
        A racing writer that inserts the same hash first is harmless (same
        content by construction), so a duplicate-key failure is swallowed
        per-row rather than failing the batch.
        """
        hashes = list(snapshots.keys())
        known = await self._known_hashes(hashes)
        if known is None:
            # Could not determine what exists. Writing blind would mean ~100
            # inserts all colliding with the unique index; a replay missing one
            # turn's text is the cheaper failure.
            return
        for h in hashes:
            if h in known:
                continue
            try:
                await self._db.insert(SNAPSHOT_TABLE, {"text_hash": h, "text": snapshots[h]})
            except Exception as e:  # noqa: BLE001 — lost race or oversized text
                logger.debug(f"[narrative.audit] snapshot {h[:12]} not stored: {e}")

    async def _known_hashes(self, hashes: Iterable[str]) -> Optional[Set[str]]:
        """Which of these hashes are already stored — keys only.

        Deliberately NOT `load_snapshots(...).keys()`: that is `SELECT *`, so it
        would drag the whole pool's MEDIUMTEXT back over the wire on every
        non-continuous turn purely to throw it away — on the synchronous path of
        `select()`, i.e. billed to every user message, and re-shipping the text
        `load_pool` just read from `narratives` in the same turn. With a
        100-narrative pool and long summaries that is hundreds of KB a turn.

        Returns None for "could not find out", which is NOT the same as the
        empty set. Conflating them makes the caller believe nothing is stored
        and insert the entire pool, every row colliding with the unique index
        and every failure swallowed one level down — a warning followed by ~100
        doomed writes, on the synchronous path, once per turn, forever. Failing
        the check costs one turn's snapshots; mistaking it for "empty" quietly
        taxes every conversation.
        """
        wanted = list(dict.fromkeys(h for h in hashes if h))
        if not wanted:
            return set()
        try:
            rows = await self._db.get_by_ids(
                SNAPSHOT_TABLE, "text_hash", wanted, fields=["text_hash"]
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(
                f"[narrative.audit] snapshot existence check failed, skipping this "
                f"turn's snapshot writes: {e}"
            )
            return None
        return {r["text_hash"] for r in rows if r}

    # ── read ────────────────────────────────────────────────────────────
    async def load_snapshots(self, hashes: Iterable[str]) -> Dict[str, str]:
        """Resolve text_hash -> text. Missing hashes are simply absent.

        The replay read path — it wants the text. Writers checking existence
        must use `_known_hashes` instead.
        """
        wanted = list(dict.fromkeys(h for h in hashes if h))
        if not wanted:
            return {}
        try:
            rows = await self._db.get_by_ids(SNAPSHOT_TABLE, "text_hash", wanted)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[narrative.audit] snapshot load failed: {e}")
            return {}
        # get_by_ids preserves INPUT order by padding misses with None
        # (db_backend_sqlite.get_by_ids: `result_map.get(id_val)`), and on the
        # very first write every hash is a miss — so this must skip None, not
        # subscript it. Getting this wrong made the whole batch fall into
        # record()'s advisory except and silently store nothing.
        return {r["text_hash"]: (r.get("text") or "") for r in rows if r}

    async def recent(
        self,
        agent_id: Optional[str] = None,
        selection_method: Optional[str] = None,
        limit: int = 50,
    ) -> List[dict]:
        """Recent audit rows, newest first, with ``candidates`` decoded.

        Ordering and the limit are pushed into SQL, not applied in Python after
        a full-table read. This table takes a row per turn, never deletes, and
        each row carries `candidates_json` (~100 candidates of id + sha256 +
        score) plus `query_text` — an agent with tens of thousands of turns
        would otherwise pull hundreds of MB into the process to return 50 rows.
        """
        try:
            filters: Dict[str, Any] = {}
            if agent_id:
                filters["agent_id"] = agent_id
            if selection_method:
                filters["selection_method"] = selection_method
            rows = await self._db.get(
                AUDIT_TABLE, filters, limit=limit, order_by="id DESC"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[narrative.audit] recent() failed: {e}")
            return []
        out = []
        for r in rows:
            r = dict(r)
            try:
                r["candidates"] = json.loads(r.get("candidates_json") or "[]")
            except (TypeError, ValueError):
                r["candidates"] = []
            out.append(r)
        return out

    # No snapshot_count() here. It existed only to let a test prove dedup, and
    # it was this file's single piece of hand-written SQL — which would have
    # dragged the repository into the "hand-written SQL needs MySQL coverage"
    # obligation for a query production never runs. The count lives in the test
    # now, and every production DB access here goes through the dialect-safe
    # client helpers (insert / get / get_by_ids).
