"""
@file_name: config.py
@author: NetMind.AI
@date: 2025-11-24
@description: Global configuration for the narrative retrieval system

All tunable parameters are centralized in this file for easy experimentation and tuning
"""
import os

from loguru import logger


def _env(name: str, default: str) -> str:
    """Read an env override for a config knob, falling back to `default`.

    Used for the narrative model / reasoning_effort knobs so two backends
    can be started side-by-side with different settings for A/B comparison
    without editing this file each time.
    """
    value = os.environ.get(name)
    return value if value else default


class NarrativeConfig:
    """Global configuration for the narrative retrieval system"""

    # ==================== Session Management ====================

    # NOTE (2026-05-20 short-term-memory continuity fix): the session timeout
    # was REMOVED. The session is the continuity anchor for the user's chat
    # box — the user can reply to a visible message minutes, hours, or days
    # later (e.g. answering a question the agent sent from a scheduled job),
    # so the anchor must persist indefinitely. Sessions are one tiny,
    # overwritten-in-place file per (agent, user); they do not grow without
    # bound, so there is nothing to time out. (Was SESSION_TIMEOUT = 600.)
    # The fast path (step_1_fast_select) honors the same rule: a live
    # anchor is reused regardless of age. It briefly reintroduced a
    # 30-minute window (2026-08-14) — removed the same day for exactly
    # the reason above.

    # ==================== Continuity Detection (LLM version) ====================

    # Continuity detection model
    # Description: LLM model used to determine topic continuity
    # Default: gpt-5.4-mini-2026-03-17 (reasoning model, fast w/ low effort).
    # Env override: NARRATIVE_CONTINUITY_MODEL
    CONTINUITY_LLM_MODEL = _env("NARRATIVE_CONTINUITY_MODEL", "gpt-5.4-mini-2026-03-17")

    # Continuity detection reasoning effort
    # Description: reasoning_effort passed to GPT-5.4 reasoning models.
    # OpenAI's chat.completions API accepts: "none" / "low" / "medium"
    # / "high" / "xhigh". Note: the openai-agents pydantic Literal still
    # lists "minimal" instead of "none" (lib lag, 2026-05); don't trust
    # it — sending "minimal" gives 400 invalid_request_error from
    # OpenAI server-side, observed in prod 2026-05-12. "low" is the
    # smallest budget that both layers accept, and benches sub-second.
    # Env override: NARRATIVE_CONTINUITY_EFFORT
    CONTINUITY_LLM_REASONING_EFFORT = _env("NARRATIVE_CONTINUITY_EFFORT", "low")

    # Narrative judge model
    # Description: LLM model used for narrative matching/judge decisions.
    # Default: gpt-5.4-mini-2026-03-17. Env override: NARRATIVE_JUDGE_MODEL
    NARRATIVE_JUDGE_LLM_MODEL = _env("NARRATIVE_JUDGE_MODEL", "gpt-5.4-mini-2026-03-17")

    # Narrative judge reasoning effort
    # Description: reasoning_effort passed to GPT-5.4 reasoning models.
    # See CONTINUITY_LLM_REASONING_EFFORT for why "low" (not "minimal").
    # Env override: NARRATIVE_JUDGE_EFFORT
    NARRATIVE_JUDGE_LLM_REASONING_EFFORT = _env("NARRATIVE_JUDGE_EFFORT", "low")

    # LLM call maximum retry count
    # Description: Number of retries when LLM API call fails
    # Recommended: 3
    CONTINUITY_LLM_MAX_RETRIES = 3

    # ==================== Narrative Matching ====================

    # ==================== Narrative Matching Thresholds (Two-tier threshold + Unified LLM judgment) ====================

    # High-confidence gate — see _narrative_impl/routing_gate.py for the full
    # rationale. Both conditions must hold before BM25 is trusted to route a
    # turn without LLM arbitration.
    #
    # Replaces NARRATIVE_MATCH_HIGH_THRESHOLD = 0.70, which compared the
    # SQUASHED score s/(s+1) and so meant "raw >= 2.33" — cleared by incidental
    # CJK character collisions. Measured on 113 real prod turns across 22
    # agents, that rule short-circuited 86.7% of routing decisions, i.e. the
    # LLM arbitration tier was effectively dead code.
    #
    # RAW_FLOOR is a NOISE filter, deliberately low — NOT a strength test.
    # Raw BM25 scales with query length (measured median top1: 5.3 for queries
    # under 40 chars vs 12-15 for over 40), so a high absolute floor
    # systematically rejects short follow-up turns regardless of whether the
    # match is right — and short follow-ups are exactly where misrouting hurts.
    # The MARGIN does the discrimination: within one query all candidates share
    # an IDF table, so the spread is meaningful even though the absolute value
    # is not.
    #
    # MARGIN_RATIO is set on an asymmetric cost: a false defer costs one helper
    # LLM call and the LLM usually confirms; a false accept poisons the thread
    # and gets locked in for several turns by the `continuous` path, which
    # re-uses session.current_narrative_id with no topic check. Prefer
    # deferring. At 2.0 the eval set short-circuits 54.9% of turns.
    NARRATIVE_MATCH_RAW_FLOOR = 3.0
    NARRATIVE_MATCH_MARGIN_RATIO = 2.0

    # Fast-path (TurnProfile bm25_top1) anchor-override floor.
    # The fast path has no continuity LLM: when the session carries a live
    # anchor (current_narrative_id), that thread is reused by default and a
    # BM25 top-1 may steal the turn away from it ONLY above this raw score.
    # Deliberately high — raw BM25 scales with query length (median top1:
    # ~5.3 under 40 chars vs 12-15 over, see RAW_FLOOR note above), so short
    # follow-ups can never clear it and stay in their thread, while a long,
    # topic-rich message that decisively matches another thread still
    # switches. Distinct from RAW_FLOOR, which is a noise filter for the
    # anchorless case. Env override: NARRATIVE_FAST_ANCHOR_OVERRIDE_FLOOR
    #
    # NOTE (2026-08-14, measured): with a live anchor the fast path never
    # CREATES a narrative. Two "trusted silence ⇒ new topic" gates were
    # tried and measured against real BM25 distributions: a 40-char gate
    # never opened for CJK (complete zh sentences are 11-15 chars), and a
    # script-independent 8-unit gate fragmented one coherent 7-turn zh
    # conversation into 5 narratives (on-topic zh continuations score
    # 1.0-3.2 — straddling RAW_FLOOR — and >=8-unit pure ACKs like
    # "嗯嗯我明白了那就这样吧" probe as silence). BM25 cannot separate
    # "new topic" from "elliptical continuation" in CJK, and the error
    # asymmetry decides: a misfiled turn is recoverable (the next
    # full-path turn re-routes via continuity; switch_narrative exists),
    # a fragmented thread is not (permanent split + an empty ChatModule
    # history for the agent mid-conversation). New threads under fast
    # mode arrive via: no live anchor (first conversation), a strong
    # override hit onto an existing thread, or the next full-path turn.
    FAST_ANCHOR_OVERRIDE_FLOOR = float(
        _env("NARRATIVE_FAST_ANCHOR_OVERRIDE_FLOOR", "12.0")
    )

    # Below high threshold: < this value, unified LLM judgment (considering both search results and default Narratives)
    # LLM will determine:
    #   - Whether it matches searched Narratives (returns a list)
    #   - Whether it matches default Narratives (returns only 1)
    #   - Or create a new Narrative

    # Whether to enable LLM-assisted matching (when score is below high threshold)
    # Description: When True, scenarios below high threshold are handled by unified LLM judgment
    #      When False, scores below high threshold directly create a new Narrative (not recommended)
    NARRATIVE_MATCH_USE_LLM = True

    # Narrative retrieval Top-K
    # Description: Returns the top K most similar Narrative candidates during retrieval
    # Recommended: 3
    # Purpose: Can put Top-3 into Context for Agent reference (optional)
    NARRATIVE_SEARCH_TOP_K = 3

    # The BM25 candidate pool's size — `load_pool`'s fetch limit on EVERY
    # path (flag on or off), not a merged-routing knob (review round 3, M3:
    # it was first filed under the merged section, where tuning it looked
    # arm-local). Ranking always runs at full pool depth (round 3, I1), so
    # raising this raises both fetch and ranking together — no second
    # constant to keep in step.
    NARRATIVE_POOL_LIMIT = int(_env("NARRATIVE_POOL_LIMIT", "100"))

    # Slice 0 instrument: record the BM25 pool on continuity turns too, where
    # `select` used to return before the retrieval tier ran. Default ON — the
    # measurement is the whole point, it costs two DB reads + one
    # snapshot-dedup SELECT (steady state ~1 INSERT), and the shadow row's
    # candidates_json carries the full pool (10KB-scale) against a setup
    # phase whose p50 is 8.5 SECONDS, and it decides nothing.
    #
    # It has a switch because every comparable governance toggle in this batch
    # has one (`NARRATIVE_DEFAULT_BUCKETS_ENABLED` below), and without it
    # turning the instrument off means a code change plus re-publishing BOTH
    # run modes (binding rule #7).
    #
    # ROLLBACK: `NARRATIVE_SHADOW_POOL_RECORD=0` and restart. The decision path
    # is untouched either way — this only stops the recording.
    #
    # ⚠ READ THIS BEFORE ANALYSING A WINDOW: with the switch off,
    # `pool_is_shadow` is 0 on every row, which is INDISTINGUISHABLE in the
    # data from "continuity turns never had a pool". A period with the switch
    # off therefore reads as "the shutter has no releasable population on
    # continuity turns" rather than "we did not look". If you turn it off,
    # write down the window — the table cannot tell you afterwards.
    # Even with the switch ON, background-triggered continuation turns
    # (job / message_bus / IM webhook, ~30% of dev turns) stay 0 by scope —
    # the instrument records user-chat turns only. Population queries must
    # pair this column with `is_user_chat`. Coverage over ALL continuation
    # turns will sit meaningfully below 100% by design (chat is 69% and
    # message_bus 30% of all dev turns, measured — but the background share
    # of CONTINUATION turns specifically has not been); read the real split
    # with GROUP BY is_user_chat, not against a fixed number.
    NARRATIVE_SHADOW_POOL_RECORD = _env("NARRATIVE_SHADOW_POOL_RECORD", "1") == "1"

    # Number of Narratives added to Context
    # Description: Upper limit of Narratives returned by select()
    # Recommended: 3 (1 main Narrative + 2 auxiliary references)
    MAX_NARRATIVES_IN_CONTEXT = 3

    # Medium continuity weighting factor
    # Description: When continuity detection judges as "medium", weight the current Session's Narrative
    # Range: 1.0-1.5
    # Recommended: 1.2 (20% boost)
    # Purpose: Bias toward continuing the current topic when uncertain
    CONTINUITY_BOOST_FACTOR = 1.2

    # ==================== Event Selection Strategy ====================

    # Most recent N Events
    # Description: Always load the Narrative's most recent N Events to ensure continuity
    # Recommended: 3
    MAX_RECENT_EVENTS = 3

    # Maximum Event count in Context
    # Description: Upper limit of total Events added to Context (after deduplication)
    # Recommended: 6
    MAX_EVENTS_IN_CONTEXT = 6

    # ==================== Default buckets (C-1 governance) ====================
    # The eight seeded "default" narratives (GreetingAndCourtesy, …) stop being
    # routing CONTAINERS when this is False — they leave the BM25 pool, leave
    # the judge's candidate menu, and are no longer seeded for new (agent,user)
    # pairs. The verdict "this turn carries no durable topic" survives as
    # `matched_category = "no_durable_topic"`. (The eight names were initially
    # kept in the judge prompts as recognition vocabulary; 2026-08-21 live
    # testing showed the taxonomy itself teaching classify-and-dump, so the
    # names are gone from ALL prompts — pinned by
    # test_judge_instructions_dropped_the_eight_category_names.)
    #
    # Why they had to go (measured, spec 2026-08-14-default-bucket-governance):
    #   - 26.4% of prod user turns and 27.0% of a real prod slice's chat turns
    #     had a bucket as their MAIN narrative, and 9080/9080 buckets platform-
    #     wide still carry their factory summary — a bucket never accumulates a
    #     retrieval surface, so a topic filed into one can never be recalled.
    #   - The eight rows also perturbed 9.7% of top-1 BM25 results just by
    #     sitting in the pool (IDF/avgdl are computed over the set handed in).
    #
    # False is the shipping value. Flipping to True restores the CONTAINER
    # side only (seeding, pool, menu offer) — it does NOT bring back the old
    # taxonomy-carrying prompts, which were removed separately on 2026-08-21;
    # a FULL rollback to the old world is this flag PLUS reverting the two
    # taxonomy-removal prompt commits. Existing bucket ROWS are never deleted
    # either way — binding rule #6.
    NARRATIVE_DEFAULT_BUCKETS_ENABLED = (
        _env("NARRATIVE_DEFAULT_BUCKETS_ENABLED", "0") == "1"
    )

    # ==================== Merged routing (one call, four answers) ==========
    # ON = BM25 runs FIRST on every turn, then either a zero-LLM shutter or ONE
    # helper call decides both questions the two-tier path asks serially ("does
    # this continue the thread?" then "so where does it go?").
    #
    # Why it is worth a flag at all (prod, 7 days, is_user_chat=1, n=189):
    # 43 turns paid for BOTH calls, at a serial p50 of 8,924ms / mean 13,004ms.
    # The non-LLM half of routing is 47.6ms mean — there is nothing else in
    # there to fix, so the only lever is the number of round trips.
    #
    # ROLLBACK IS THE FLAG, and it rolls back exactly one thing: the ROUTING
    # STRUCTURE (who decides, and in what order). Flipping it back to "0"
    # restores the continuity → judge pair and stops the merged prompt from
    # being built; it does NOT undo anything else in this batch, and nothing
    # else in this batch needs undoing:
    #   * the merged prompt constants are a NEW file section, unreferenced when
    #     the flag is off;
    #   * the shared prompt blocks (`routing_blocks.py`) render the continuity
    #     tier's and the judge's text byte for byte — pinned by test;
    #   * the audit columns are additive and simply stay NULL;
    #   * ⚠ ONE thing changed for BOTH arms and is NOT flag-controlled:
    #     candidates_json[].raw_score precision. Ranking now runs at full pool
    #     depth on every path (round 3, I1), so rank-7+ pool members carry
    #     their real BM25 score where they used to record a hard 0.0. Prompts
    #     are byte-identical either way; the AUDIT series has a step change at
    #     this deploy. Any "how many candidates scored" analysis over a window
    #     that straddles it must split at the deploy timestamp — same reading
    #     rule as NARRATIVE_SHADOW_POOL_RECORD's window note above.
    # (Stated this precisely because the bucket flag's comment claimed a full
    # rollback it could not deliver, and PR #361 review caught it.)
    #
    # Retirement (delete the flag AND the two-call path, not the new one)
    # when ALL of: ① the merged exam passes its pre-registration (p07 hijack
    # 0/22 veto, DIVERTED=0, calls −≥15%); ② the flag has run ON in prod for
    # 14 days with no flag rollback; ③ prod audit shows all five verdicts plus
    # at least one merged_fallback_* on merged_call=1 rows (an untravelled
    # fallback is unverified); ④ the Owner rules the two-call path need not be
    # revivable. Full checklist: todo/2026-08-26-merged-routing-flag-
    # retirement-condition.md (author-local).
    NARRATIVE_MERGED_ROUTING_ENABLED = (
        _env("NARRATIVE_MERGED_ROUTING_ENABLED", "0") == "1"
    )

    # ---- the merged prompt's input budget (READ-SIDE ONLY) ----
    # Caps on what the prompt SHOWS. Nothing here rewrites a stored field: same
    # narrative row, same session, same message. All head-preserving — see
    # `routing_blocks.clamp_head`.
    #
    # Why hard caps at all, when the two-tier path had none: continuity and the
    # judge each read a SUBSET of these fields, and the anchor block was only
    # rendered on turns that had an anchor. The merged prompt renders the whole
    # set on EVERY routed turn, so an unbounded field is no longer one tier's
    # bad turn — it is every turn's latency and every turn's bill.

    # The agent's previous reply. The tension to keep in mind if you retune it:
    # the referent of a follow-up ("讲第一个" / "the first one") almost always
    # sits in the FIRST sentences of that reply, which is why the clamp keeps
    # the head — but a long reply's tail is also where a closing question can
    # live ("要不要我继续?"). 1500 covers the p90 of prod agent replies; raising
    # it costs latency on every merged turn, so raise it against measured
    # misroutes, not on principle.
    MERGED_PREV_RESPONSE_MAX_CHARS = int(_env("NARRATIVE_MERGED_PREV_RESPONSE_MAX_CHARS", "1500"))

    # The anchored thread's summary. `current_summary` has a soft bound in the
    # updater prompt and NO hard bound anywhere — a verbose model walks straight
    # through it, and this block is rendered on every merged turn.
    MERGED_ANCHOR_SUMMARY_MAX_CHARS = int(_env("NARRATIVE_MERGED_ANCHOR_SUMMARY_MAX_CHARS", "2000"))

    # Agent awareness. Deliberately smaller than the message cap: awareness is
    # the agent's standing persona, and routing needs only enough of it to read
    # the domain the agent works in.
    MERGED_AWARENESS_MAX_CHARS = int(_env("NARRATIVE_MERGED_AWARENESS_MAX_CHARS", "1500"))

    # This turn's message, and (same cap, same reason) the previous turn's — a
    # generous ceiling that exists only to stop a pathological paste from
    # dominating the call. A real message never approaches it.
    MERGED_QUERY_MAX_CHARS = int(_env("NARRATIVE_MERGED_QUERY_MAX_CHARS", "4000"))

    # PARTICIPANT threads shown. A prefix, never a re-ranking: the ORDER is the
    # P0-4 priority rule, so trimming to fit must not reorder.
    MERGED_PARTICIPANT_MAX_CANDIDATES = int(_env("NARRATIVE_MERGED_PARTICIPANT_MAX_CANDIDATES", "8"))

    # Keyword menu rows. Three, as in the two-tier judge (`search_results[:3]`)
    # — this batch changes the decider, not the menu size, so a menu-size change
    # would confound the arm.
    MERGED_MENU_SIZE = int(_env("NARRATIVE_MERGED_MENU_SIZE", "3"))

    # ==================== Narrative LLM Dynamic Update ====================
    # Use LLM to update Narrative metadata every N Events (name, current_summary,
    # actors, topic_keywords, dynamic_summary). Default 1 = every Event; raise to
    # 3-5 to reduce LLM call costs. (Moved back from the package-root config.py
    # 2026-07-24 — narrative/ was its only consumer, the root file is gone.)
    NARRATIVE_LLM_UPDATE_INTERVAL = 1

    # LLM model used for updates
    # Description: LLM model used for generating Narrative summaries and metadata.
    # Default: gpt-5.4-mini-2026-03-17. Env override: NARRATIVE_UPDATE_MODEL
    NARRATIVE_LLM_UPDATE_MODEL = _env("NARRATIVE_UPDATE_MODEL", "gpt-5.4-mini-2026-03-17")

    # Narrative update reasoning effort.
    # Description: summary updates run post-turn in the background, so they
    # are not on the critical path — "low" is fine. See
    # CONTINUITY_LLM_REASONING_EFFORT for why "low" (not "minimal").
    # Env override: NARRATIVE_UPDATE_EFFORT
    NARRATIVE_LLM_UPDATE_REASONING_EFFORT = _env("NARRATIVE_UPDATE_EFFORT", "low")

    # Number of recent Events considered during LLM update
    # Description: Generates summaries based on the most recent N Events
    # Recommended: 5
    NARRATIVE_LLM_UPDATE_EVENTS_COUNT = 5


    # Summary maximum length
    # Description: Maximum character count for topic_hint
    # Recommended: 200
    SUMMARY_MAX_LENGTH = 200

    # Hard cap on a stored `narrative_info.description`, applied in
    # `NarrativeCRUD.create` — the ONE funnel all three writers pass through
    # (routing's `create_from_query`, the LLM's `create_narrative` signal in
    # `step_4_persist_results`, and the HTTP route). Fixing only the routing
    # door would leave two open, and the LLM one takes its text straight from
    # tool arguments.
    #
    # 512, deliberately NOT SUMMARY_MAX_LENGTH: the eight curated default-bucket
    # descriptions reach the LLM judge, and `GreetingAndCourtesy` is 206
    # characters — a 200 cap would silently truncate frozen prompt content.
    # 512 clears every bucket and still clamps only the pathological tail
    # (prod non-default: 55% are under 200 chars, 21% are over 1,500,
    # max 198,398). A test pins the "does not clip a bucket" property so a
    # later "let's align these two constants" cannot quietly break it.
    DESCRIPTION_MAX_LENGTH = 512

    # ==================== Hierarchical Structure (Reserved for Phase 2) ====================

    # Whether to enable hierarchical tree structure
    # Description: Set to False in Phase 1, all Narratives are flat
    # Set to True in Phase 2 to enable Root/Children structure
    ENABLE_HIERARCHICAL_STRUCTURE = False

    # Beam Search width (used in tree-based retrieval)
    # Description: Number of candidates retained per level during tree search
    # Recommended: 3
    # Only effective when ENABLE_HIERARCHICAL_STRUCTURE=True
    BEAM_SEARCH_WIDTH = 3

    # Root level similarity threshold
    # Description: Minimum matching threshold between Query and Root Narrative
    # Recommended: 0.70
    # Only effective when ENABLE_HIERARCHICAL_STRUCTURE=True
    ROOT_MATCH_THRESHOLD = 0.70

    # Child level similarity threshold
    # Description: Minimum matching threshold between Query and Child Narrative
    # Recommended: 0.75
    # Only effective when ENABLE_HIERARCHICAL_STRUCTURE=True
    CHILD_MATCH_THRESHOLD = 0.75

    # ==================== Narrative Splitting (Reserved for Phase 2) ====================

    # Whether to enable automatic splitting
    # Description: Set to False in Phase 1, no automatic splitting
    ENABLE_AUTO_SPLIT = False

    # Maximum Event count (split trigger condition 1)
    # Description: When a Narrative's Event count exceeds this value, trigger split detection
    # Recommended: 20
    MAX_EVENTS_PER_NARRATIVE = 20

    # Topic coherence threshold (split trigger condition 2)
    # Description: When average similarity of last N Events to Narrative topic falls below this, trigger split
    # Recommended: 0.60
    TOPIC_COHERENCE_THRESHOLD = 0.60

    # Coherence check window (Event count)
    # Description: Number of recent Events considered when checking topic coherence
    # Recommended: 3
    COHERENCE_CHECK_WINDOW = 3

    # ==================== Debugging and Logging ====================

    # Whether to enable verbose logging
    # Description: Outputs detailed information about continuity detection, similarity computation, etc.
    # Development phase: True
    # Production environment: False
    ENABLE_VERBOSE_LOGGING = True

    # Whether to log similarity scores
    # Description: Records all similarity computation results in Narrative metadata
    # Purpose: For subsequent analysis and parameter tuning
    LOG_SIMILARITY_SCORES = True


def _reject_untested_flag_combination(cfg: "NarrativeConfig") -> None:
    """Refuse to boot with bucket governance OFF-ness inverted under merging.

    Merged routing is built on a property bucket governance provides: with
    `NARRATIVE_DEFAULT_BUCKETS_ENABLED = False` a legacy default bucket is not a
    reusable anchor (`is_reusable_anchor`), so it can never occupy the merged
    prompt's anchor slot and `continue_anchor` can never re-pin a turn to a
    container whose retrieval surface never updates.

    Turn both on and that guarantee is gone — and NOTHING has ever measured that
    world: every arm, every dry run and every prod number behind the merged
    design was taken with buckets off. Failing here costs a startup; failing
    silently costs a routing decision nobody can explain, on the exact shape
    (frozen anchor, identity wash) that produced the p07 hijack.
    """
    if cfg.NARRATIVE_DEFAULT_BUCKETS_ENABLED and cfg.NARRATIVE_MERGED_ROUTING_ENABLED:
        # Deliberately still at import time: moving this to per-process
        # preflights was weighed (review minor 9) and rejected — one missed
        # entrypoint would disarm the gate for that process entirely, which is
        # worse than an import-chain traceback. The CRITICAL line below is the
        # readable part ops will actually see, printed before the raise.
        logger.critical(
            "STARTUP CONFIGURATION ERROR: NARRATIVE_MERGED_ROUTING_ENABLED=1 "
            "and NARRATIVE_DEFAULT_BUCKETS_ENABLED=1 cannot be combined — "
            "set one of them to 0 in .env and restart."
        )
        raise RuntimeError(
            "NARRATIVE_MERGED_ROUTING_ENABLED=1 with "
            "NARRATIVE_DEFAULT_BUCKETS_ENABLED=1 is an untested combination: "
            "merged routing relies on a default bucket NOT being a reusable "
            "anchor. Turn one of them off."
        )


# Export config instance (singleton)
config = NarrativeConfig()

# At import, i.e. at process start — the earliest point at which this can be
# answered, and long before a user's turn depends on the answer.
_reject_untested_flag_combination(config)
