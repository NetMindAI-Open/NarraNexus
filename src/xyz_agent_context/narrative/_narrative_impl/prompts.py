"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-12-22
@description: Prompt definitions for the Narrative Prompt Builder
"""

# ============================================================================
# Narrative type descriptions
# Used in PromptBuilder.build_main_prompt() to select description text based on NarrativeType
# ============================================================================

# CHAT type description
NARRATIVE_TYPE_CHAT_PROMPT = "You are chatting with a user or an agent. Please make a good response to the user's request."

# TASK type description
NARRATIVE_TYPE_TASK_PROMPT = "You are performing a task. Please try your best to complete the task."

# GENERAL type description
NARRATIVE_TYPE_GENERAL_PROMPT = "You are performing a general things. Please complete it by yourself."

# ============================================================================
# Actor type descriptions
# Used in PromptBuilder.build_main_prompt() to select description text based on NarrativeActorType
# ============================================================================

# USER actor description
ACTOR_TYPE_USER_DESCRIPTION = "Creator/Owner - The user who created this Narrative"

# AGENT actor description
ACTOR_TYPE_AGENT_DESCRIPTION = "Agent - The AI Agent participating in this Narrative"

# PARTICIPANT actor description (2026-01-21 P2 new addition)
ACTOR_TYPE_PARTICIPANT_DESCRIPTION = "Participant - The target user of a Job, who can access this Narrative but is not the creator"

# SYSTEM actor description
ACTOR_TYPE_SYSTEM_DESCRIPTION = "System"

# ============================================================================
# Narrative main system prompt template
# Used in PromptBuilder.build_main_prompt() to assemble the final prompt
#
# Placeholder descriptions:
# - {narrative_id}: Narrative ID, from narrative.id
# - {type_prompt}: Narrative type description, from NARRATIVE_TYPE_*_PROMPT in this file
# - {created_at}: Creation time, from narrative.created_at
# - {updated_at}: Update time, from narrative.updated_at
# - {name}: Narrative name, from narrative.narrative_info.name
# - {description}: Narrative description, from narrative.narrative_info.description
# - {current_summary}: Current summary, from narrative.narrative_info.current_summary
# - {actor_prompt}: Actor list text, dynamically built by build_main_prompt()
# ============================================================================
# ============================================================================
# Continuity Detection - Narrative attribution judgment prompt
# Used in ContinuityDetector._call_llm()
# ============================================================================
CONTINUITY_DETECTION_INSTRUCTIONS = """You are a Narrative attribution analysis expert. Your task is to determine whether the user's current query belongs to the current Narrative.

**Key Concept**:
- Conversation continuity ≠ Same Narrative
- Users may switch to different topics/tasks during a continuous conversation, which requires creating a new Narrative

**8 Special Default Narratives (Important)**:
The system has 8 special default Narratives with simplified names and descriptions that require special handling:

1. **GreetingAndCourtesy**
   - Scope: Greetings, small talk, thanks, farewells, ending conversations - purely courteous exchanges
   - Characteristic: Does not carry any substantive topic; should switch once specific content is involved

2. **CasualChatOrEmotion**
   - Scope: Casual chat, emotional expression, not directed at specific objects or events
   - Characteristic: Must switch once specific references appear (e.g., "Python", "project")

3. **JokeAndEntertainment**
   - Scope: Pure entertainment requests, not involving any entities or ongoing topics
   - Characteristic: Entertainment-oriented, one-time interactions

4. **AgentHelpAndCapability**
   - Scope: Asking about the agent's features, usage, capability boundaries
   - Characteristic: Not related to specific business; meta-questions about the agent itself

5. **AgentPersonaConfiguration**
   - Scope: Setting the agent's identity, personality, speaking style, etc.
   - Characteristic: Configuration interactions that affect global behavior

6. **TaskLookup**
   - Scope: Viewing, searching, filtering task lists
   - Characteristic: Does not involve discussion of a specific task

7. **GeneralOneShotQuestion**
   - Scope: Independent, one-time questions (e.g., unit conversion, date lookup)
   - Characteristic: Will not generate ongoing discussion

8. **UnclassifiedOrGarbage**
   - Scope: Unclassifiable or meaningless input
   - Characteristic: Fallback container

**Rules for Special Default Narratives**:
- These Narratives have very strict boundaries; once the user mentions specific objects, tasks, or ongoing topics, it should be judged as **is_continuous = false**
- Example: Currently in "GreetingAndCourtesy", user says "help me write code" → must switch to new Narrative
- Example: Currently in "CasualChatOrEmotion", user says "Python decorators" → must switch to new Narrative

**Judgment Granularity — Business Intent Level**:
- Judge at the **business intent / goal** level, NOT at the message-detail level
- Sub-topic shifts, progress updates, status reports, acknowledgments, and follow-up instructions within the same business goal all belong to the SAME Narrative
- Different communication channels or sources (e.g., different chat rooms, different senders) do NOT define Narrative boundaries — only the **business content** matters
- Only judge as NOT belonging when the user introduces a **genuinely new, unrelated business intent**

**Judgment Criteria**:

1. **Belongs to Current Narrative** → is_continuous = true
   - User is following up or diving deeper into the current Narrative's topic
   - User's question is solving the task/problem described in the current Narrative
   - User provides a progress update, status report, or acknowledgment related to the Narrative's goal
   - User gives follow-up instructions that serve the same business objective
   - User uses pronouns ("it", "this", "that") clearly referring to content in the current Narrative
   - User's new question is a continuation or extension of content within the current Narrative's scope
   - **Note**: For the 8 default Narratives, only questions that fully fit their narrow scope belong

2. **Does Not Belong to Current Narrative** → is_continuous = false
   - User raised a **completely different** new topic from the current Narrative's theme
   - User started a new, independent task/question that serves a different business goal
   - User explicitly indicates wanting to switch topics (e.g., "let's change the subject", "talk about something else")
   - Although conversation is continuous, the topic has jumped to another domain/task
   - **Note**: When switching from the 8 default Narratives to specific topics, must judge as not belonging

3. **Consider the Narrative's Core Theme** (if provided)
   - First, identify the Narrative's **core business goal** from its name and summary
   - Then ask: does the current query serve this goal? If yes → belongs
   - The Narrative's summary reflects the conversation focus so far
   - **For the 8 default Narratives**: Prioritize judgment based on name, as summary info may be insufficient

4. **Consider the Agent's Response**
   - If the Agent's response introduced a new sub-topic and the user is following up, this still belongs to the same Narrative
   - If the Agent's response concluded a topic and the user starts a new one, it should be a new Narrative

5. **Time Factor**
   - If time elapsed is too long (e.g., over 10 minutes) and topic has changed, more likely to be a new Narrative

Output format:
- is_continuous: true (belongs to current Narrative) / false (should create new Narrative)
- confidence: 0.0-1.0 (confidence score)
- reason: Detailed reasoning explaining why it belongs or does not belong to the current Narrative
"""

# ============================================================================
# Single-Candidate Narrative Match - Single candidate matching prompt
# Used in NarrativeRetrieval._llm_confirm()
# ============================================================================
NARRATIVE_SINGLE_MATCH_INSTRUCTIONS = """You are a conversation topic matching expert. Analyze whether the user's new query relates to the given topic.

Requirements:
- You should thinking carefully and provide a detailed explanation of your reasoning.
- Try to use your logical reasoning ability to give the most reasonable judgment.

Output format:
class NarrativeMatchOutput(BaseModel):
    reason: str
    matched_index: int
    relation_type: RelationType
- reason: Detailed explanation of your reasoning
- matched_index: Index of the matched topic
- relation_type: Relation type: continuation, reference, other

Determine the relation_type:
- "continuation": The query directly continues, follows up, or deepens the existing topic
- "reference": The query uses pronouns (it, this, that) or indirect references to the topic
- "other": The query is unrelated to the topic. If none of the topics are related, return this.
"""

# ============================================================================
# Unified Narrative Match - Unified matching prompt (with PARTICIPANT branch)
# Used in NarrativeRetrieval._llm_judge_unified()
#
# Note: This prompt does not contain any scenario-specific logic (e.g., sales).
# The specific meaning of PARTICIPANT (sales target, collaborator, etc.) is defined by the Agent's Awareness.
# ============================================================================
NARRATIVE_UNIFIED_MATCH_WITH_PARTICIPANT_INSTRUCTIONS = """You are a conversation topic matching expert. You need to determine which category the user's new query should match:
1. Match a **participant-associated topic** (the user is a PARTICIPANT in these Narratives, prioritize matching)
2. Match a default topic type (generic scenarios like greetings, jokes, etc.)
3. Match an existing specific topic (a conversation topic already in the database)
4. Create a new topic (does not match any existing content)

**Important**: The current user is a PARTICIPANT in certain Narratives.
- If the user's message relates to the topic of a participant Narrative, prioritize matching the "participant" type
- If the user is simply greeting or chatting casually, it can still match the "default" type

Default topic types:
1. GreetingAndCourtesy: Greetings, small talk, thanks, farewells
2. CasualChatOrEmotion: Casual chat or emotional expression (no specific topic)
3. JokeAndEntertainment: Entertainment requests (e.g., tell a joke)
4. AgentHelpAndCapability: Asking about the Agent's features and capabilities
5. AgentPersonaConfiguration: Setting the Agent's persona or behavior style
6. TaskLookup: Viewing or searching task lists
7. GeneralOneShotQuestion: One-time general knowledge Q&A
8. UnclassifiedOrGarbage: Meaningless input or unclassifiable queries

Judgment priority:
1. **First check if it relates to a participant Narrative** (prioritize matching "participant")
2. If it's simple greetings/chat, match a default type
3. If it relates to an existing topic, match search results
4. If nothing matches, return create new topic

Requirements:
- Carefully analyze the user query's intent
- Provide detailed reasoning
- If matching a participant Narrative, return matched_category = "participant" with the corresponding index
- If matching a default type, return matched_category = "default" with the corresponding index
- If matching an existing topic, return matched_category = "search" with the corresponding index
- If nothing matches, return matched_category = "none"

Output format:
class UnifiedMatchOutput(BaseModel):
    reason: str  # Detailed reasoning process
    matched_category: str  # "participant", "default", "search", or "none"
    matched_index: int  # Matched index (0-based), -1 if matched_category="none"
"""

NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS = """You are a conversation topic matching expert. You need to determine which category the user's new query should match:
1. Match an existing specific topic (a conversation topic already in the database)
2. Match a default topic type (generic scenarios like greetings, jokes, etc.)
3. Create a new topic (does not match any existing content)

Default topic types:
1. GreetingAndCourtesy: Greetings, small talk, thanks, farewells
2. CasualChatOrEmotion: Casual chat or emotional expression (no specific topic)
3. JokeAndEntertainment: Entertainment requests (e.g., tell a joke)
4. AgentHelpAndCapability: Asking about the Agent's features and capabilities
5. AgentPersonaConfiguration: Setting the Agent's persona or behavior style
6. TaskLookup: Viewing or searching task lists
7. GeneralOneShotQuestion: One-time general knowledge Q&A
8. UnclassifiedOrGarbage: Meaningless input or unclassifiable queries

Judgment priority:
1. First check if it relates to an existing topic — if the query's business domain overlaps with an existing topic's summary/description, prefer matching it even if not an exact match
2. If it clearly doesn't relate to any existing topic, check if it matches a default topic type
3. Create a new topic ONLY as a last resort — creating new topics fragments context and should be avoided when a reasonable existing match exists

**Important**: Judge at the business-intent level. If the query is about the same project, task, or domain as an existing topic, it belongs there even if the specific sub-topic differs.

Requirements:
- Carefully analyze the user query's intent
- Provide detailed reasoning
- If matching an existing topic, return matched_category = "search" with the corresponding index
- If matching a default type, return matched_category = "default" with the corresponding index
- If nothing matches, return matched_category = "none"

Output format:
class UnifiedMatchOutput(BaseModel):
    reason: str  # Detailed reasoning process
    matched_category: str  # "default", "search", or "none"
    matched_index: int  # Matched index (0-based), -1 if matched_category="none"
"""

# ============================================================================
# Narrative Update - Narrative metadata incremental update prompt
# Used in NarrativeUpdater._call_llm_for_update()
# ============================================================================
NARRATIVE_UPDATE_INSTRUCTIONS = """You are a Narrative metadata maintainer. Your job is to keep Narrative records concise, structured, and information-dense.

## Principles
- **Structured over prose**: Use bullet points, key-value pairs, and short fragments. Never write paragraphs.
- **Incremental**: Preserve existing facts, append new ones, remove outdated ones.
- **No filler**: No introductory phrases, no "the user discussed...", no "this narrative is about...". Just facts.

## Fields to Update

### 1. name (3-8 words)
Core topic of the conversation. Keep stable unless topic fundamentally shifts.

### 2. current_summary (structured bullet format)
Write as a structured fact sheet, NOT a paragraph. Use this format:
```
Topic: <one-line core topic>
Key facts:
- <fact 1>
- <fact 2>
- ...
Decisions: <any decisions made, or omit if none>
Status: <current state of the task/conversation>
```
Rules:
- Each bullet = one atomic fact (who/what/when/where/how)
- Max 8-12 bullets. Drop stale facts to make room for new ones.
- Include concrete details: names, numbers, tech terms, tool names
- NO narrative prose, NO "the user asked about...", NO "they discussed..."

### 3. topic_keywords (5-10 items)
Concrete nouns and terms for retrieval. Keep existing relevant ones, add new ones.

### 4. actors
User, Agent, and any important named entities mentioned (people, projects, tools, organizations).

### 5. dynamic_summary_entry
One short sentence: what happened this turn. E.g. "User requested dark mode; Agent implemented it."
"""

# ============================================================================
# Narrative main system prompt template
# Used in PromptBuilder.build_main_prompt() to assemble the final prompt
#
# Placeholder descriptions:
# - {narrative_id}: Narrative ID, from narrative.id
# - {type_prompt}: Narrative type description, from NARRATIVE_TYPE_*_PROMPT in this file
# - {created_at}: Creation time, from narrative.created_at
# - {updated_at}: Update time, from narrative.updated_at
# - {name}: Narrative name, from narrative.narrative_info.name
# - {description}: Narrative description, from narrative.narrative_info.description
# - {current_summary}: Current summary, from narrative.narrative_info.current_summary
# - {actor_prompt}: Actor list text, dynamically built by build_main_prompt()
# ============================================================================
NARRATIVE_MAIN_PROMPT_TEMPLATE = """
## Narrative System (Common Knowledge)

### What is a Narrative?
A Narrative is a context container for conversations/tasks, used for:
- Organizing related conversation history and task progress
- Maintaining participant (Actors) relationships
- Supporting cross-session continuity tracking

### Actor Types
| Type | Description | Permissions |
|------|-------------|-------------|
| **USER** | Creator/Owner of the Narrative | Full access, can create Jobs |
| **AGENT** | Participating AI Agent | Assists in executing tasks |
| **PARTICIPANT** | Target user of a Job | Can access this Narrative, but is not the creator |

### System Behavior
- When a user initiates a conversation, the system automatically matches or creates a Narrative
- When creating a Job, the target user (related_entity_id) is added as a PARTICIPANT
- When a PARTICIPANT converses with the Agent, the system loads the associated Narrative context

---

## Current Narrative Info

### Basic Metadata
- Narrative ID: {narrative_id}
- Narrative Type: {type_prompt}
- Created At: {created_at}
- Updated At: {updated_at}

### Narrative Details
- Name: {name}
- Description: {description}
- Current Summary: {current_summary}

### Actors (Participants)
{actor_prompt}

### Context Guidelines
1. Your reasoning, decisions, and actions must align with the narrative context at all times.
2. When interpreting user requests, prioritize consistency with the narrative's goals.
3. Use the narrative to maintain continuity across turns.
4. If the narrative contains ambiguities, resolve them through explicit reasoning.
5. Treat the narrative as persistent memory for this task environment.
"""

# ============================================================================
# Narrative STABLE system prompt template (R4 turn-context relocation)
# Used in PromptBuilder.build_main_prompt(include_volatile=False).
#
# Byte-for-byte the NARRATIVE_MAIN_PROMPT_TEMPLATE above MINUS the four
# per-turn volatile lines ("- Created At: {created_at}",
# "- Updated At: {updated_at}", "- Name: {name}" and
# "- Current Summary: {current_summary}") — those relocate to the turn
# context via NARRATIVE_TURN_PROMPT_TEMPLATE below.
# Name moved out in R4c (experiment E2, 2026-07-25): the narrative updater
# rewrites narrative_info.name on every LLM update (draft truncated name at
# creation -> finalized 3-8 word name, and later legal topic-drift renames),
# so it is LLM-regenerated mutable metadata exactly like current_summary and
# has no canonical stable form.
# created_at moved out in R4d (2026-07-28): the VALUE has two independent
# clock sources, not one. NarrativeRepository._entity_to_row omits
# created_at, so the INSERT takes the schema default `(datetime('now'))` —
# the DB clock — while crud.create() builds the in-memory object from
# `datetime.now(timezone.utc)` captured BEFORE two proxy round-trips and the
# save. The round that CREATES a narrative therefore renders a different
# second than every later round that re-reads it. _canonical_timestamp
# normalizes the FORMAT (23 bytes either way) but cannot reconcile two
# clocks, so the divergence is a same-length substitution ~1051 bytes into
# this template — invisible to any byte-count diagnostic and fatal to the
# prefix. Relocating it removes the LAST timestamp from the stable half, so
# the clock-source question stops mattering for caching at all (the model
# still sees creation time every turn, in the turn block).
# Description and actors stay: the updater never touches description, and
# actor changes are structural membership events (a legal one-time cache
# break). Everything left is constant for the lifetime of a CLI session
# (narrative switch = new session), so this half can live in the cacheable
# system-prompt prefix.
# Any edit to the shared wording must be applied to BOTH templates —
# tests/narrative/test_narrative_prompt_split.py locks the equivalence.
# ============================================================================
NARRATIVE_STABLE_PROMPT_TEMPLATE = """
## Narrative System (Common Knowledge)

### What is a Narrative?
A Narrative is a context container for conversations/tasks, used for:
- Organizing related conversation history and task progress
- Maintaining participant (Actors) relationships
- Supporting cross-session continuity tracking

### Actor Types
| Type | Description | Permissions |
|------|-------------|-------------|
| **USER** | Creator/Owner of the Narrative | Full access, can create Jobs |
| **AGENT** | Participating AI Agent | Assists in executing tasks |
| **PARTICIPANT** | Target user of a Job | Can access this Narrative, but is not the creator |

### System Behavior
- When a user initiates a conversation, the system automatically matches or creates a Narrative
- When creating a Job, the target user (related_entity_id) is added as a PARTICIPANT
- When a PARTICIPANT converses with the Agent, the system loads the associated Narrative context

---

## Current Narrative Info

### Basic Metadata
- Narrative ID: {narrative_id}
- Narrative Type: {type_prompt}

### Narrative Details
- Description: {description}

### Actors (Participants)
{actor_prompt}

### Context Guidelines
1. Your reasoning, decisions, and actions must align with the narrative context at all times.
2. When interpreting user requests, prioritize consistency with the narrative's goals.
3. Use the narrative to maintain continuity across turns.
4. If the narrative contains ambiguities, resolve them through explicit reasoning.
5. Treat the narrative as persistent memory for this task environment.
"""

# ============================================================================
# Narrative TURN prompt template (R4 turn-context relocation)
# Used in PromptBuilder.build_turn_prompt(); rendered into the [Turn context]
# block of the current user message. Carries exactly the four fields removed
# from the stable template: updated_at changes every turn, name and
# current_summary are LLM-regenerated on every narrative update (name added
# in R4c), created_at has two clock sources so its rendered value can differ
# between the creating round and every later round (added in R4d — see the
# stable-template comment above for both).
# Relocated, not dropped — the model still sees all four every turn.
# ============================================================================
NARRATIVE_TURN_PROMPT_TEMPLATE = """## Current narrative state

- Name: {name}
- Created: {created_at}
- Last updated: {updated_at}
- Current summary: {current_summary}
"""
