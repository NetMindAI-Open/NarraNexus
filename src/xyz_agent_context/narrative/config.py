"""
@file_name: narrative_config.py
@author: NetMind.AI
@date: 2025-11-24
@description: Global configuration for the narrative retrieval system

All tunable parameters are centralized in this file for easy experimentation and tuning
"""
import os


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

    # High confidence threshold: >= this value for direct match without LLM confirmation
    # Recommended: 0.70 (empirical data: clearly related retrospective queries typically score >= 0.7)
    # Tuning suggestion: Set a higher threshold to ensure accuracy of high-confidence direct returns
    NARRATIVE_MATCH_HIGH_THRESHOLD = 0.70

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

    # ==================== Narrative LLM Dynamic Update ====================
    # Note: NARRATIVE_LLM_UPDATE_INTERVAL has been moved to global config xyz_agent_context/config.py

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


# Export config instance (singleton)
config = NarrativeConfig()
