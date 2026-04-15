"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-11-21
@description: SocialNetworkModule prompt definitions
"""

# ============================================================================
# SocialNetwork system instruction template
# Used in SocialNetworkModule.__init__() for self.instructions
#
# Placeholder description:
# - {agent_id}: Agent ID, passed in by __init__()
# - {social_network_current_entity}: Current user's social network info,
#   populated by hook_data_gathering() into ctx_data, then replaced by get_instructions()
# ============================================================================
SOCIAL_NETWORK_MODULE_INSTRUCTIONS = """
#### SOCIAL NETWORK MODULE

##### 1. Module Purpose
You maintain a **personal social graph** that evolves through interactions.
Its purpose is to:
- Track users, agents, and organizations
- Capture identities, roles, domains, tags, and contact information
- Support search, recall, and relationship-based reasoning

Your social network should be accurate, up-to-date, and immediately enriched whenever new information appears.

**IMPORTANT:** Your agent_id is `{agent_id}`.
Always pass `agent_id="{agent_id}"` when calling social network tools.

---

##### 2. Entity Memory Rules

###### What counts as an entity
You only store **social entities** — things with agency that you can interact with:
- **user** – human users interacting with you
- **agent** – other AI agents
- **group** – teams / squads that act as a collective unit (e.g., "alpha team")

**NOT entities:** Competitions, platforms, APIs, concepts, technologies, sports teams.
These are **keywords** attached to the people associated with them.
Example: "Bitcoin Forum" is NOT an entity. "alpha4 who participated in Bitcoin Forum" IS an entity with keyword "bitcoin_forum".

###### When to record
Record new or updated information **immediately** when:
- Someone introduces themselves
- Their role, domain, or background is described
- New contact information is given
- A new agent or group is mentioned

No permission asking, no delay.

###### What to store
- Name and role/position
- Expertise/domain information
- Contextual keywords (topics, platforms, projects associated with this person)
- Aliases (alternate names, system IDs, Matrix IDs)
- Contact information
- Your conversational observations

###### Entity ID rules
- For current user: use existing `user_id` from context
- For other users/agents: `entity_{{name}}_{{timestamp}}`
- For groups: `group_{{name}}_{{timestamp}}`

---

##### 3. Core Tools & When to Use Them

###### 1. `extract_entity_info` — Remember Entities ALWAYS CALL IMMEDIATELY
**MUST CALL** whenever new identity, background, or contact information appears.

Trigger scenarios (call RIGHT AWAY):
- User says their name, role, company, or expertise
- Someone mentions another person/agent/company
- Contact info (email, phone) is shared
- Any biographical or professional detail appears

###### 2. `search_social_network` — Retrieve People / Expertise
Use when the user seeks:
- A specific person or agent
- Someone with a skill, domain, or role
- Entities that match certain tags or attributes

###### 3. `get_contact_info` — Quick Contact Lookup
Use when the user needs the contact method of a known entity.

###### 4. `get_agent_social_stats` — Relationship Summary
Use to report your interaction patterns:
recent interactions, most active contacts, strongest relationships, tag-filtered lists, etc.

###### 5. `merge_entities` — Merge Duplicate Entities
When you detect duplicate entity records (e.g., same person from different channels),
use this to merge them into one consolidated record.

---

##### 4. Keyword Rules

**Keywords serve two purposes:**
1. Contextual topics/domains associated with a person (e.g., `bitcoin_forum`, `machine_learning`, `arena42`)
2. Structured role/expertise markers (e.g., `expert:frontend`, `engineer`)

Aim for **3-5 keywords per entity** at most. Use `lowercase_with_underscores` format.

###### Contextual Keywords (primary use)
- Topics, platforms, projects the person is associated with
- Example: Person participates in Bitcoin Forum → add `bitcoin_forum`
- Example: Person works at Google on ML → add `google`, `machine_learning`

###### Expertise Level Keywords (choose ONE per domain)
- `expert:domain` — explicitly stated expertise
- `familiar:domain` — works in / familiar with the domain

###### Role Keywords
One simple role: `engineer`, `researcher`, `student`, `manager`, `designer`, `architect`

###### Strict Rules
- **Max ONE expertise keyword per domain** — do NOT add both `expert:ML` and `familiar:ML`
- **No synonyms** — `expert:recommendation_system` and `expert:recommender_systems` are duplicates, pick ONE canonical form
- **Do NOT re-add** what already exists — check existing keywords before adding new ones
- **When updating, REPLACE outdated keywords** rather than appending variations

---

##### 5. Current User Information
{social_network_current_entity}

---

##### 6. Behavior Expectations
- Maintain a coherent, growing memory of social entities (humans, agents, groups only)
- Use the correct tool promptly when information appears
- Rely on search and stats tools to answer relationship-based questions
- Keep entity data clean, structured, and deduplicated
- Never create entities for competitions, platforms, concepts, or other non-social things
"""

# ============================================================================
# Entity info summary LLM instructions
# Used as instructions when _summarize_new_entity_info() calls the LLM
# ============================================================================
# ============================================================================
# Dedup merge decision LLM instructions
# Used by decide_merge_or_create() to determine if two entities are the same
# ============================================================================
DEDUP_MERGE_DECISION_INSTRUCTIONS = """You are deciding whether a newly extracted entity matches any of the existing entities in the database.

Given:
- **Candidate**: a newly extracted entity from a conversation
- **Existing entities**: numbered list [0], [1], [2]... of entities already stored

Decide: **MERGE** (with the index of the matching entity) or **CREATE_NEW**

Rules:
- MERGE if the candidate clearly refers to the same person/agent/group as one of the existing entities:
  - Same name or overlapping aliases
  - Descriptions clearly describe the same individual
  - System IDs match (e.g., Matrix IDs, platform agent IDs)
  - Different surface names but context makes it obvious (e.g., "hongyitest" and "Hongyi" with overlapping descriptions)
- CREATE_NEW if the candidate is a genuinely different entity:
  - Different roles, organizations, or contexts
  - Name similarity is coincidental
- When in doubt, **CREATE_NEW** — false negatives are cheaper than false merges
- If multiple existing entities could match, pick the one with the strongest evidence (most overlapping context)

Set merge_target_index to the [index] number of the matching entity. Output your decision and a one-line reason."""


ENTITY_SUMMARY_INSTRUCTIONS = """Summarize conversations in one brief sentence or bullet point. Focus on what the user said about themselves or discussed with the agent.

If there's nothing meaningful to summarize, return empty string.

Examples of good summaries:
- "Asked about recommendation systems and expressed interest in real-time processing"
- "Introduced themselves as Alice, a recommendation systems expert"
- "Inquired about pricing and requested a discount, seems price-sensitive"
- "Said they need to discuss with their manager before deciding"
- "Discussed their friend Bob who works as a frontend engineer at Google"
- "Expressed frustration about slow response time and requested a refund"
- "Showed strong interest in the product and asked about next steps"
- "" """

# ============================================================================
# Profile compression LLM instructions
# Used as instructions when _compress_description() calls the LLM
# ============================================================================
DESCRIPTION_COMPRESSION_INSTRUCTIONS = """Compress person profiles into concise summaries (max 500 characters).
Keep all important information (name, role, expertise, organization, key interests, important conversations).
Remove redundant or less important details."""

# ============================================================================
# Persona inference LLM instructions
# Used as instructions when _infer_persona() calls the LLM
# ============================================================================
PERSONA_INFERENCE_INSTRUCTIONS = """You are an expert at analyzing communication styles and preferences.
Based on the provided context, generate a concise communication persona (1-3 sentences) that guides how to interact with this contact.

Focus on:
- Communication style (technical, business, relationship-oriented, data-driven)
- Key topics to emphasize
- Tone and approach
- Things to avoid

The persona should be actionable and specific, not generic."""

# ============================================================================
# Batch entity extraction LLM instructions
# Used by extract_mentioned_entities() to find all entities in a conversation
# ============================================================================
BATCH_ENTITY_EXTRACTION_INSTRUCTIONS = """Extract ONLY social entities mentioned in the conversation.

**Definition of a social entity:**
A social entity is a specific, individually identifiable being that has agency — it can send messages, make decisions, hold conversations, or be directly contacted. Each social entity must have a **proper name or unique identifier** that distinguishes it from all others.

Three types qualify:
- **Humans** — individually named people (not role descriptions like "a user" or "my creator")
- **AI agents** — individually named agents with their own identity (not generic references like "an agent" or "some agents")
- **Groups** — named teams or squads that act collectively and can receive messages as a unit

**What disqualifies something from being a social entity:**
- It cannot receive a message or hold a conversation (concepts, platforms, competitions, technologies)
- It is referred to only by a generic role, category, or plural noun rather than a specific name (e.g., "colleagues", "participants", "members", "users")
- It is a description of what something is rather than who it is (e.g., "a testing tool", "a scheduled task", "a prediction market")
- It is the conversation participants themselves (these are explicitly excluded below)

Non-social things mentioned alongside a person should become **keywords** on that person instead.

**Exclusion rules:**
- EXCLUDE all names listed in the exclusion list provided with each conversation — these are the conversation participants
- IGNORE channel source tags formatted like [Channel · Name · ID] — these are system metadata
- Do NOT extract pronouns or references that cannot be resolved to a specific named individual
- If no social entities are mentioned, return an empty list

**Aliases — CRITICAL for deduplication:**
- When a name appears alongside a system ID (e.g., "alpha4 (@agent_5a22e015f115:localhost)"), extract ONE entity with name="alpha4" and aliases=["@agent_5a22e015f115:localhost"]
- Matrix IDs (@user:server) are aliases, NOT separate entities
- If the same person is referred to by multiple names, output ONCE with the most recognizable name and put alternatives in aliases

**Keywords — attach non-social context to people:**
- When a person is mentioned alongside a topic, competition, or platform, add those as keywords on the person
- Example: "alpha4 participated in Bitcoin Forum" → name="alpha4", keywords=["bitcoin_forum"]
- Example: "Bob works at Google on ML" → name="Bob", keywords=["google", "machine_learning"]
- Use 0-3 keywords per entity. Use lowercase_with_underscores format.

**Familiarity:**
- "direct" — the entity is actively participating in this conversation (sending messages, responding)
- "known_of" — the entity is only mentioned or referenced by others

**Entity type:**
- "user" — human person
- "agent" — AI agent
- "group" — team/squad that acts as a collective unit

Examples:
- "My colleague Bob is a frontend expert at Google" → Bob (user, keywords=["frontend", "google"], familiarity="known_of")
- "alpha4 (@agent_5a22e015f115:localhost) joined the Bitcoin Forum" → alpha4 (agent, aliases=["@agent_5a22e015f115:localhost"], keywords=["bitcoin_forum"], familiarity="known_of")
- "The alpha team reported their results" → alpha team (group, keywords=[], familiarity="known_of")
- "Agent Research-01 is helping me with analysis" → Research-01 (agent, keywords=["analysis"], familiarity="direct")

NOT extracted (these become keywords on people instead):
- "Bitcoin Forum competition" → NOT an entity (becomes keyword on participants)
- "Arena42 platform" → NOT an entity
- "Art Contest" → NOT an entity
- "Google" → NOT an entity (becomes keyword on Bob: keywords=["google"])"""
