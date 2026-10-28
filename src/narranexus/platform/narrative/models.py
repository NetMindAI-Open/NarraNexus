"""
@file_name: models.py
@author: NetMind.AI
@date: 2025-12-22
@description: Unified data models for the Narrative module

Merged from:
- narrative.py: Narrative, NarrativeInfo, NarrativeType, etc.
- event.py: Event, EventLogEntry, TriggerType, etc.
- models.py (original): ConversationSession, ContinuityResult, NarrativeSearchResult

Data model categories:
1. Event related: TriggerType, EventLogEntry, Event
2. Narrative related: NarrativeType, NarrativeActor, NarrativeInfo, DynamicSummaryEntry, Narrative
3. Session related: ConversationSession, ContinuityResult, NarrativeSearchResult
"""

from typing import List, Dict, Any, Optional
from datetime import datetime
from enum import Enum
from pydantic import BaseModel

# Import ModuleInstance (from schema, to avoid duplicate definitions)
from narranexus.platform.schema.module_schema import ModuleInstance
from narranexus.platform.schema.open_enum import OpenStrEnum


# =============================================================================
# Event Related Models
# =============================================================================

class TriggerType(OpenStrEnum):
    """
    Trigger type of an Event — WHAT kind of surface started this run.

    Since 2026-07-31 the members mirror ``schema.hook_schema.WorkingSource``
    values 1:1 (step 0 maps ``ctx.working_source`` straight through), so
    ``events.trigger`` is an honest per-source label instead of "everything
    is chat". Read-sides rely on it: the sidebar preview excludes
    MESSAGE_BUS, the chat panel's active-run auto-attach accepts only
    CHAT/MANYFOLD, dashboards group by it.

    Open like ``WorkingSource`` (plugin platform batch 4): the core members
    are bound below, every IM channel's member arrives through
    ``WorkingSource.register`` (its descriptor), so a plugin channel's runs
    are labelled by their own surface instead of degrading to CHAT.
    """

    CHAT: "TriggerType"
    TASK: "TriggerType"
    API: "TriggerType"
    TOOL: "TriggerType"
    MESSAGE_BUS: "TriggerType"
    JOB: "TriggerType"
    A2A: "TriggerType"
    CALLBACK: "TriggerType"
    SKILL_STUDY: "TriggerType"
    MANYFOLD: "TriggerType"
    OTHER: "TriggerType"


TriggerType._add("CHAT", "chat")  # Chat trigger
TriggerType._add("TASK", "task")  # Task trigger
TriggerType._add("API", "api")  # API trigger
TriggerType._add("TOOL", "tool")  # Agent proactively invokes a tool trigger
TriggerType._add("MESSAGE_BUS", "message_bus")  # Team group-chat (message bus) reply
TriggerType._add("JOB", "job")  # Scheduled job (JobTrigger)
TriggerType._add("A2A", "a2a")  # Agent-to-Agent call
TriggerType._add("CALLBACK", "callback")  # Dependency-chain callback after Job completion
TriggerType._add("SKILL_STUDY", "skill_study")  # Skill study run
TriggerType._add("MANYFOLD", "manyfold")  # Manyfold platform via the OpenAI-compat endpoint
TriggerType._add("OTHER", "other")


class EventLogEntry(BaseModel):
    """
    Event log entry

    Records each step of operation in the Agent Loop
    """
    timestamp: datetime  # Timestamp
    type: str  # Type: thinking, tool_call, tool_result, message_output, etc.
    content: Any  # Specific content


class Event(BaseModel):
    """
    Event represents a complete process "from trigger to final output"

    It represents a traceable reasoning and action process in the system,
    and is the basic unit for Narrative growth and updates.

    According to the design document:
    - Event contains: ID, Trigger, Env Context, Module Set, Event Log, Final Output
    - Event is the basic unit for Narrative growth and updates
    """
    id: str  # Randomly generated unique ID
    trigger: TriggerType  # Event trigger type
    trigger_source: str  # Detailed trigger source info, e.g., "user_123", "task_456"
    env_context: Dict[str, Any]  # Event execution environment info (model, agent framework, execution params, etc.)
    module_instances: List[ModuleInstance]  # All Module instances loaded during this event
    event_log: List[EventLogEntry]  # Detailed record of each reasoning/call step in the Event
    final_output: str  # Final response content produced when the Event ends
    created_at: datetime  # Event creation time
    updated_at: datetime  # Event update time

    # Association info
    narrative_id: Optional[str] = None  # Associated Narrative ID (if any)
    agent_id: str  # Associated Agent ID
    user_id: Optional[str] = None  # Associated User ID (if applicable)



# =============================================================================
# Narrative Related Models
# =============================================================================

class NarrativeType(Enum):
    """
    Narrative type
    """
    CHAT = "chat"
    TASK = "task"
    OTHER = "other"


class NarrativeActorType(Enum):
    """
    Narrative actor type

    - USER: Creator/owner of the Narrative
    - AGENT: Agent participant
    - SYSTEM: System participant
    - PARTICIPANT: Target user of a Job, can access the associated Narrative but is not the creator
    """
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system"
    PARTICIPANT = "participant"  # 2026-01-21: Support for target customers in sales scenarios


class NarrativeActor(BaseModel):
    """
    Narrative actor
    """
    id: str  # Actor's ID
    type: NarrativeActorType  # Actor's type


class NarrativeInfo(BaseModel):
    """
    Narrative basic information
    """
    name: str  # Name of the narrative
    description: str  # Description of the narrative
    current_summary: str  # Summary of the narrative
    actors: List[NarrativeActor]  # List of actors in the narrative


class DynamicSummaryEntry(BaseModel):
    """
    A single entry in the Dynamic Summary

    Records a short summary of each Event, arranged chronologically
    """
    event_id: str  # Event ID
    summary: str  # Short summary of the Event
    timestamp: datetime  # Event time
    references: List[str] = []  # Referenced other event_ids


# Summaries that mean "the updater has not written one yet". `NarrativeCRUD.create`
# pre-fills `current_summary` rather than leaving it empty, and
# `default_narratives` does the same for the eight buckets, so "the summary is
# non-empty" is NOT the same question as "this thread has a real record".
#
# This matters precisely for the case the retirement rule exists to protect: the
# async updater can fail (D-9 helper outage), and a thread whose summary is still
# the creation placeholder has nothing but its description to describe itself. A
# naive non-empty test would retire the birth certificate at the instant of
# birth and that thread would score zero against the very query that created it.
#
# ONE definition, imported by both the writer and this reader — two copies of
# the literal would rot apart silently, and the only symptom would be new
# threads quietly becoming unfindable.
PROVISIONAL_SUMMARY_PREFIXES = (
    "Newly created Narrative: ",
    "This is a default ",
)


class Narrative(BaseModel):
    """
    Narrative = Routing Metadata for a storyline

    Core concepts:
    - Narrative does not store Memory (content), only routing information (index)
    - Memory is managed by each Module through EventMemoryModule
    - narrative_id is the unique identifier for Module Instances

    Field categories:
    - Identity: id, type, agent_id
    - Routing Index: topic_keywords (BM25, via searchable_text())
    - Orchestration Config: active_instances, instance_history_ids
    - References Only: event_ids
    - Metadata: created_at, updated_at
    """
    # ===== Identity =====
    id: str  # Randomly generated unique ID
    type: NarrativeType  # Narrative type
    agent_id: str  # Associated Agent ID

    # ===== Core Content =====
    narrative_info: NarrativeInfo  # Narrative basic info (name, description, central summary)

    # ===== Orchestration Config =====
    # Main Chat Instance (deprecated, 2026-01-21 P1-1)
    # No longer uses a fixed main_chat_instance_id; each user gets an independent ChatModule instance via _ensure_user_chat_instance()
    main_chat_instance_id: Optional[str] = None  # Deprecated, retained only for database compatibility

    # Instance management
    active_instances: List[ModuleInstance] = []  # Currently active Module instances
    instance_history_ids: List[str] = []  # Completed/failed instance IDs

    # ===== References Only =====
    event_ids: List[str]  # List of event IDs in the narrative (chronologically ordered)

    # ===== Dynamic Summary =====
    dynamic_summary: List[DynamicSummaryEntry] = []  # Dynamic summary list

    # ===== Env Variables =====
    env_variables: Dict[str, Any] = {}  # Environment variables

    # ===== Routing Index =====
    topic_keywords: List[str] = []  # Topic keywords
    # Written ONCE by `_create_narrative` (the truncated first query) and never
    # updated since the 2026-06-09 unified-memory refactor removed its update
    # machinery. It is therefore creation-time provenance, NOT current state:
    # 84% empty on the local dev DB, and where non-empty it can be months stale
    # or a `[:50]` cut through the middle of an open_id. Display it as "what
    # started this thread" (backend/routes/me.py does) — never feed it to a
    # routing decision or a prompt; use `narrative_info.current_summary`.
    topic_hint: str = ""  # Creation-time first query, frozen

    # ===== Metadata =====
    created_at: datetime  # Narrative creation time
    updated_at: datetime  # Narrative update time
    round_counter: int = 0  # Round counter

    # ===== Association Info =====
    related_narrative_ids: List[str] = []  # Related Narrative IDs

    # ===== Special Markers =====
    is_special: str = "other"  # Special marker field, default value is "other"

    def description_if_unsummarised(self) -> str:
        """The birth certificate — readable ONLY until the medical record exists.

        `description` is written once at creation from the raw triggering input
        and is never rewritten by the updater, yet it sits in the BM25 index and
        in the continuity prompt. Measured on all 1,381 non-default prod
        narratives (2026-08-20): **291 (21.1%) are over 1,500 characters and the
        longest is 198,398** — a thread born on a 5KB scheduled-task prompt has
        that 5KB welded into its retrieval surface forever. BM25 computes IDF and
        avgdl over the candidate pool itself, so one such document both crushes
        every normal candidate's length normalisation and hands itself a large
        pool of matchable tokens: offline re-scoring of 630 real decisions put
        the bypass rate at 41.0% for pools containing one against 14.5% without.

        FULL RETIREMENT, not truncate-and-keep-reading. A truncated fossil is
        still a fossil: it still asserts, in the present tense, a topic the
        thread may have left months ago.

        The condition is "the thread has a REAL summary", NOT "the updater has
        run": the updater is async and can fail, so a thread born during a
        helper outage never gets one. Keying on the record rather than on the
        writer makes the rule self-healing — record written, birth certificate
        retires; record stillborn, birth certificate keeps standing in and the
        thread does not go invisible.

        "Real" excludes the creation placeholders (see
        `PROVISIONAL_SUMMARY_PREFIXES`): `NarrativeCRUD.create` pre-fills
        `current_summary`, so a literal non-empty test would retire the birth
        certificate at the instant of birth and defeat the self-healing branch
        entirely.

        Every read of the raw field is on an allow-list pinned by
        `tests/narrative/test_description_retirement.py`, so a fourth read site
        cannot quietly bypass this.
        """
        info = self.narrative_info
        summary = (getattr(info, "current_summary", "") or "").strip()
        if summary and not summary.startswith(PROVISIONAL_SUMMARY_PREFIXES):
            return ""
        return getattr(info, "description", "") or ""

    def searchable_text(self) -> str:
        """The text that represents this narrative to search — the ONE definition.

        Two callers must agree on it or the system contradicts itself about what
        a narrative is about:
          - `_narrative_impl/retrieval.load_pool` — the per-turn BM25 routing pool
          - `_narrative_impl/crud._index_narrative` — the projection into the
            unified memory index that `remember` searches
        Both rank with the same `bm25_rank`, so a drift between them would make
        recall and routing disagree while every test still passed. They had
        already drifted in form (`" ".join` vs `"\\n".join`) before this was
        pulled up here — equivalent only because the tokenizer splits on
        whitespace, i.e. one tokenizer change away from being a real bug.

        It lives on the model because retrieval imports crud; a shared helper in
        either of them would be a circular import.

        `description` enters only through `description_if_unsummarised()`, which
        retires it once the thread has a real summary. Before 2026-08-20 the raw
        field went in unconditionally and a 198KB creation-time prompt could own
        an entire pool's IDF table.
        """
        info = self.narrative_info
        return " ".join(
            p for p in (
                getattr(info, "name", "") or "",
                getattr(info, "current_summary", "") or "",
                # Retires as soon as there is a summary — see
                # `description_if_unsummarised`.
                self.description_if_unsummarised(),
                " ".join(self.topic_keywords or []),
            ) if p
        )


# =============================================================================
# Session Related Models
# =============================================================================

class ConversationSession(BaseModel):
    """
    Conversation Session

    Used to track continuous conversations between a user and an Agent,
    determining continuity between queries.

    Lifecycle:
    - Created: On the user's first query (or first agent message to the user)
    - Updated: anchor (last_query / last_response / current_narrative_id)
      updated after each user-visible turn — see step_1 / step_4
    - Persistent: sessions never expire (the chat-box continuity anchor must
      survive arbitrary idle gaps; the 10-min timeout was removed 2026-05-20)
    """
    # ===== Core Identity =====
    session_id: str  # Session unique ID (format: sess_xxxxxxxx)
    user_id: str  # User ID
    agent_id: str  # Agent ID

    # ===== Time Info =====
    created_at: datetime  # Session creation time
    last_query_time: datetime  # Time of the last query

    # ===== Continuity Tracking =====
    last_query: str = ""  # Text content of the last query
    last_response: str = ""  # Content of the last Agent response
    current_narrative_id: Optional[str] = None  # Currently active Narrative ID

    # ===== Statistics =====
    query_count: int = 0  # Total number of queries in this session


class ContinuityResult(BaseModel):
    """
    Narrative Attribution Detection Result

    Used for ContinuityDetector to return detection results.

    Note: This is not just about determining conversation continuity,
    but whether the current query belongs to the current Narrative.
    Conversation continuity != Belonging to the same Narrative.
    """
    # ===== Core Result =====
    is_continuous: bool  # Whether it belongs to the current Narrative
    confidence: float  # Confidence (0-1)
    reason: str  # Judgment reason

    # ===== Detailed Info (for debugging) =====
    rule_score: Optional[float] = None  # Quick rule score
    semantic_score: Optional[float] = None  # Semantic similarity score
    weighted_score: Optional[float] = None  # Weighted final score


class NarrativeSearchResult(BaseModel):
    """
    Narrative Search Result

    Used to return retrieved Narratives with their relevance scores
    """
    narrative_id: str  # Narrative ID
    similarity_score: float  # Squashed score s/(s+1), for display / LLM prompt
    rank: int  # Rank (1 = most relevant)
    # Un-squashed BM25 score. The high-confidence gate reads THIS: s/(s+1) is
    # monotonic but compresses the spread between candidates, which is the only
    # comparable signal we have (IDF is computed per candidate set, so absolute
    # values carry no cross-agent meaning). Participant narratives, which enter
    # the pool with a synthetic neutral similarity and never had a BM25 score,
    # keep 0.0 here so they cannot trip the gate.
    raw_score: float = 0.0
    # WHY this candidate scored what it scored, carried forward to the LLM
    # arbitration tier. A score alone is not just uninformative, it is
    # misleading: request-frame characters (帮/查/一/下) accumulate real BM25
    # weight under per-character CJK tokenization, so a semantically unrelated
    # narrative can reach a squashed 0.91 with zero topic-bearing overlap. The
    # judge runs exactly when the gate found candidates CROWDED, i.e. when
    # distinguishing substance from politeness is the whole decision — see
    # `_narrative_impl/retrieval.rank_pool_full`, which fills both from the
    # same BM25 pass that produced `raw_score` (no extra IO, no extra DB
    # read). Participant narratives never went through BM25 and stay empty.
    # `matched_snippet` is ALSO empty by design beyond rank_pool_full's
    # snippet_depth head — any prompt consumer of deep rows must backfill it
    # (the pattern: landings.build_menu_candidates; round 9, minor 2).
    matched_terms: List[str] = []  # Query terms by descending contribution
    matched_snippet: str = ""  # Context windows where the top terms occur


class RoutingCandidate(BaseModel):
    """One narrative as it appeared in the BM25 candidate pool at decision time.

    ``text_hash`` points at the exact searchable text this narrative carried
    when it was scored — NOT what it carries now. The async LLM updater
    rewrites ``narrative_info.name`` / ``current_summary`` / ``topic_keywords``
    wholesale on (almost) every turn and keeps no history, so re-reading the
    `narratives` table later reconstructs a pool that never existed.
    """
    narrative_id: str
    text_hash: str          # sha256 of the scored text; resolves via narrative_text_snapshots
    raw_score: float        # un-squashed BM25 — the number the gate reads
    is_default: bool = False        # is_special == "default"
    is_participant: bool = False    # entered the pool via the P0-4 participant query


class RoutingAudit(BaseModel):
    """Everything needed to explain — and exactly replay — one routing decision.

    Deliberately carries the FULL candidate pool, not top-K: ``bm25_rank``
    computes IDF and avgdl over the set it is handed, so a partial pool
    reproduces different scores. See tests/narrative/test_routing_audit.py,
    which fails if this is ever trimmed.
    """
    # ── inputs ──────────────────────────────────────────────────────────
    agent_id: str
    user_id: str
    query_text: str                 # the retrieval anchor actually matched on
    trigger: str = ""               # events.trigger — message_bus was 30% of dev turns
    is_user_chat: bool = True       # False ⇒ this run must not move the session anchor

    # ── tier 1: continuity ──────────────────────────────────────────────
    continuity_ran: bool = False
    continuity_is_continuous: Optional[bool] = None
    continuity_confidence: Optional[float] = None   # computed today, then discarded
    continuity_reason: str = ""

    # ── tier 2: BM25 + gate ─────────────────────────────────────────────
    candidates: List[RoutingCandidate] = []
    gate_short_circuit: Optional[bool] = None
    gate_reason: str = ""
    gate_top1_raw: Optional[float] = None
    gate_top2_raw: Optional[float] = None
    gate_margin: Optional[float] = None
    # WHY the score gate needs its own column now that the anchor rule can
    # override it: `gate_short_circuit` keeps its original meaning — "this turn
    # skipped the judge" — so the two are NOT duplicates. `bypass_score_gate`
    # is floor+margin ALONE, which is the series the next layer has to
    # calibrate against. Without it, the day the anchor rule shipped would be
    # the day the score-gate distribution stopped accumulating in prod, and the
    # decision that needs it would have no data.
    bypass_score_gate: Optional[bool] = None
    # Which rule decided, as a stable code: anchor_match | anchor_miss |
    # no_anchor | score_gate | participant_present | background_scope |
    # no_candidates. Joined against `judge_category` this answers "what did the
    # judge actually say about the turns the anchor rule refused to let
    # through" — i.e. whether the rule is paying for itself.
    bypass_reason: str = ""
    # Slice 0. True when the pool on this row was built for the RECORD ONLY and
    # decided nothing — a continuity turn, where `select` used to return before
    # the retrieval tier ran at all.
    #
    # Without it every gate aggregate silently mixes two populations. With it —
    # AND `is_user_chat = 1`, see below — `WHERE pool_is_shadow = 0` is
    # "decisions" and `= 1` is "what the shutter
    # would have said on the turns continuity already answered" — which is the
    # measurement the merged-routing design needs and could not get: the
    # shutter's releasable population is currently bounded at 6%-39%, a 3x band
    # that is reconstruction slack, not signal, precisely because these rows
    # carried no pool.
    #
    # NOTE the deliberate asymmetry with `gate_short_circuit`: that column means
    # "the gate skipped the judge", and on a shadow row the gate skipped
    # nothing, so it stays NULL exactly as it is today (binding rule #6 — an
    # existing column does not quietly change meaning). The hypothetical verdict
    # goes to `bypass_score_gate` / `bypass_reason`, which have no legacy
    # readers.
    #
    # SCOPE (user-chat only): background-triggered continuation turns (job /
    # message_bus / IM webhook, ~30% of dev turns) are NOT recorded — they
    # keep the column's default 0 while carrying no pool and NULL gate
    # columns. So `= 0` alone holds two populations; the discriminator is
    # this column PLUS `is_user_chat`. Any cross-population query must add
    # `is_user_chat = 1`. Coverage reads (`pool_is_shadow=1` over ALL
    # continuation turns) will sit meaningfully below 100% by design —
    # background triggers are ~30% of all dev turns (measured), but their
    # share of CONTINUATION turns has not been; read the real split with
    # GROUP BY is_user_chat rather than comparing against a fixed number.
    # (A third, negligible source of 0 on a user-chat continuation row: the
    # recorder itself failed — identifiable ONLY by the accompanying
    # [narrative.shadow_pool] warning; the row itself is shaped exactly
    # like a switch-off row.)
    pool_is_shadow: bool = False

    # ── merged routing (one call instead of two) ─────────────────────────
    # Filled only when NARRATIVE_MERGED_ROUTING_ENABLED is on. On the two-call
    # path the write side stores 0 / "" (merged_call, merged_verdict,
    # merged_truncated) and NULL for the rest (merged_ms, merged_input_chars,
    # anchor_*); rows written BEFORE this deploy are NULL throughout. So a
    # filter must be NULL-safe: `merged_call = 0` evaluates NULL to false and
    # silently drops every pre-deploy row — which is the entire baseline
    # window. Use `COALESCE(merged_call, 0) <> 1` — the one form valid on BOTH
    # dialects (`IS NOT 1` parses only on SQLite; MySQL throws 1064, review
    # round 6, I1); same rule for `pool_is_shadow`.
    #
    # `merged_call` marks the PATH, not the LLM: a turn the shutter released
    # took the merged path and asked nobody, so it carries merged_call=1 with
    # `merged_verdict` empty and `merged_ms` NULL.
    merged_call: bool = False
    # continue_anchor | match | participant | new | no_topic | failed.
    # `failed` is deliberately a value rather than an absence: "the model was
    # asked and could not be used" and "no model was asked" are different rows,
    # and only the first one is a provider problem.
    merged_verdict: str = ""
    # SELF time of the merged call — it nests nothing. Stated because
    # `retrieve_ms` DOES nest `judge_ms` and that ambiguity sent two readers to
    # the same wrong conclusion ("routing spends 6 seconds in the database");
    # see todo/2026-08-25-retrieve-ms-nests-judge-ms.md.
    merged_ms: Optional[int] = None
    # EVERY character the call sent — instructions AND user input. The x-axis
    # of the latency model: instructions vary by variant and the variant
    # correlates with the turn shape, so a user_input-only count would bias
    # the slope (review round 2, I2), and the input budgets are only
    # convertible to milliseconds if production records what it actually sent.
    merged_input_chars: Optional[int] = None
    # Comma-separated section codes that hit their read-side cap
    # (prev_response / prev_query / anchor_summary / awareness / query /
    # participants). Empty when nothing was clamped — a silently shortened
    # prompt is one nobody can explain afterwards.
    merged_truncated: str = ""

    # ── the anchor's standing in BM25 (the §3.2 instrument) ──────────────
    # Merging removes a defence that was never named: on the two-call path a
    # continuity turn never built a menu, so a foreign thread could not reach
    # the prompt. Now it can, and the anchor is injected unconditionally to
    # compensate. These three columns are the only way to ever measure how often
    # that injection was the sole reason the anchor was on the ballot —
    # reconstructing it later is impossible, because the async updater rewrites
    # the scored text with no history.
    #
    # rank: 1-based among candidates that scored, NULL when the anchor scored
    # nothing (which is 8.2%-49.3% of continuity turns in the replay arms).
    anchor_bm25_rank: Optional[int] = None
    anchor_raw_score: Optional[float] = None
    # Would the anchor have been on the menu WITHOUT the unconditional
    # injection? False here is the case §3.2 exists for.
    anchor_in_menu: Optional[bool] = None

    # ── tier 3: LLM arbitration ─────────────────────────────────────────
    judge_ran: bool = False
    judge_category: str = ""        # participant | default | search | none
    judge_matched_id: Optional[str] = None
    judge_reason: str = ""

    # ── cost ────────────────────────────────────────────────────────────
    # Milliseconds per tier, joined to the decision that paid for them. The
    # `[TIMED] narrative.*` lines already measure these, but only into loguru:
    # they rotate away and cannot be aggregated, so "how long does arbitration
    # take, compared to a short-circuit" had no answer.
    #
    # None means THIS TIER DID NOT RUN — never zero. A short-circuited decision
    # skips the judge entirely, and storing 0 there would drag every "cost of
    # arbitration" query toward nothing, destroying the exact comparison these
    # columns exist to make.
    # SHADOW ROWS (pool_is_shadow=1, slice 0) change one dimension:
    # `retrieve_ms` there holds ONLY the instrument's own tier-2 cost — the
    # judge never ran. Same column, two magnitudes; any cross-population cost
    # aggregate MUST filter on pool_is_shadow first, or the continuation
    # majority's ~13ms rows dilute "how expensive is arbitration" exactly the
    # way the paragraph above warns a stored 0 would. `keyword_ms` is the one
    # cost column with an identical definition in both populations.
    # MERGED ROWS (merged_call=1) are a THIRD population with the same shape as
    # a shadow row and none of its caveats: `retrieve_ms` holds the BM25 pass
    # only, because on that path the LLM is a separate tier with its own column
    # (`merged_ms`) — so the filter is
    # `COALESCE(pool_is_shadow,0) <> 1 AND COALESCE(merged_call,0) <> 1`
    # (NULL-safe on both dialects; `= 0` drops every pre-deploy NULL row —
    # the whole baseline window — and `IS NOT 1` is SQLite-only)
    # for "tiers 2+3 together", and `merged_ms` for the merged call's own cost.
    # Three magnitudes in one column is the point at which it stops being worth
    # sharing; a future batch should split out `retrieve_self_ms` rather than
    # add a fourth (todo/2026-08-25-retrieve-ms-nests-judge-ms.md).
    continuity_ms: Optional[int] = None   # tier 1 LLM (continuity detect)
    retrieve_ms: Optional[int] = None     # tiers 2+3 together (retrieve_top_k); shadow AND merged rows: tier 2 only
    keyword_ms: Optional[int] = None      # BM25 pool load + rank
    judge_ms: Optional[int] = None        # tier 3 LLM (unified match)

    # ── outcome ─────────────────────────────────────────────────────────
    selection_method: str = ""
    retrieval_method: str = ""
    chosen_narrative_id: Optional[str] = None
    is_new: bool = False


class NarrativeSelectionResult(BaseModel):
    """
    Narrative Selection Result

    Contains the selected Narrative list and selection reason.
    Used for passing complete selection information in step_1_select_narrative.
    """
    narratives: List["Narrative"] = []  # Selected Narrative list
    selection_reason: str = ""  # Selection reason (human-readable)
    selection_method: str = ""  # Selection method: continuous, high_confidence, llm_confirmed, new_created
    is_new: bool = False  # Whether a new Narrative was created
    best_score: Optional[float] = None  # Best match score (if any)
    scores: Dict[str, float] = {}  # Per-narrative similarity scores (narrative_id → score)
    retrieval_method: str = ""  # Retrieval method: "session" (continuity) | "keyword" (BM25)

    # The judge's "this turn carries no durable topic" verdict (C-1). It is a
    # LABEL about the turn, not a destination: the retrieval tier returns it
    # with an EMPTY narrative list and NarrativeService.select decides where the
    # turn lands (anchor-first — reuse the live thread, else create on durable
    # surfaces, else run bare). It also travels to step_4, where it means "file
    # the event but do NOT let this turn rewrite the thread's retrieval
    # surface" — a greeting must never rename the work it interrupted.
    no_durable_topic: bool = False

    # ===== Routing audit (E1) — transient, never persisted on this object =====
    # The retrieval tier fills the BM25/gate/judge half; NarrativeService.select
    # adds the continuity half and the outcome, then writes one row. Carried
    # here rather than returned separately so no call site can forget it.
    audit: Optional["RoutingAudit"] = None
    audit_snapshots: Dict[str, str] = {}  # text_hash -> scored text, for the snapshot store
