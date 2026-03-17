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
You only store three types:
- **user** – human users interacting with you
- **agent** – other AI agents
- **organization** – companies / institutions mentioned in conversation

###### When to record
Record new or updated information **immediately** when:
- Someone introduces themselves
- Their role, domain, or background is described
- New contact information is given
- A new agent or organization is mentioned

No permission asking, no delay.

###### What to store
- Name and role/position (or industry for organizations)
- Expertise/domain information
- Stable tags representing skill level or role
- Contact information
- Your conversational observations

###### Entity ID rules
- For current user: use existing `user_id` from context
- For other users/agents: `entity_{{name}}_{{timestamp}}`
- For organizations: `org_{{name}}_{{timestamp}}`

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

###### 5. `contact_agent` — Send Message to Another Entity
Routes messages through the best available channel (Matrix, Slack, etc.).
Automatically selects channel based on entity's contact_info.

###### 6. `check_channel_updates` — Cross-Channel Status Check
Check for recent updates across all registered communication channels.
Use when user asks "any new messages?" or "check my channels".

###### 7. `merge_entities` — Merge Duplicate Entities
When you detect duplicate entity records (e.g., same person from different channels),
use this to merge them into one consolidated record.

---

##### 4. Tagging Rules

**IMPORTANT — Tags must be concise and minimal.**
Tags are expensive metadata. Only add a tag when it carries clear, lasting signal.
Aim for **3-5 tags per entity** at most. Do NOT add tags redundantly.

###### Expertise Level Tags (choose ONE per domain)
- `expert:domain` — explicitly stated expertise
- `familiar:domain` — works in / familiar with the domain
- `interested:domain` — learning or exploring the domain

###### Role Tags
One simple role: `engineer`, `researcher`, `student`, `manager`, `designer`, `architect`

###### Intent Tags (only when clearly observed)
- `intent:high_interest` / `intent:low_interest`
- `intent:price_sensitive` / `intent:urgent`
- `intent:decision_maker` / `intent:influencer`

###### Sales Stage Tags
- `stage:initial_contact` / `stage:interested` / `stage:evaluating`
- `stage:negotiating` / `stage:committed` / `stage:closed_won` / `stage:closed_lost`

###### Strict Rules
- **Max ONE expertise tag per domain** — do NOT add both `expert:ML` and `familiar:ML`
- **Max ONE sales stage tag** — replace the old one, never accumulate
- **No synonyms** — `expert:recommendation_system` and `expert:recommender_systems` are duplicates, pick ONE canonical form
- **No sub-domains when parent exists** — if `expert:ML` exists, do NOT add `expert:deep_learning`
- **Do NOT re-tag** what is already tagged — check existing tags before adding new ones
- **When updating, REPLACE outdated tags** rather than appending new variations

---

##### 5. Current User Information
{social_network_current_entity}

---

##### 6. Behavior Expectations
- Maintain a coherent, growing memory of social entities
- Use the correct tool promptly when information appears
- Rely on search and stats tools to answer relationship-based questions
- Keep entity data clean, structured, and deduplicated
"""

# ============================================================================
# Entity info summary LLM instructions
# Used as instructions when _summarize_new_entity_info() calls the LLM
# ============================================================================
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
BATCH_ENTITY_EXTRACTION_INSTRUCTIONS = """Extract all people, agents, or organizations mentioned in the conversation.

Rules:
- EXCLUDE the primary speaker (the user/entity directly talking)
- IGNORE channel source tags formatted like [Channel · Name · ID] or [Channel · Name · ID · RoomID] — these are system metadata markers, not conversation participants to extract
- Only extract entities explicitly named or described in the conversation content
- Do NOT infer entities that are not mentioned
- For each entity: provide name, type (user/agent/organization), and a brief summary
- If no other entities are mentioned, return an empty list

Tagging Rules (CRITICAL — be minimal):
- Only add tags when the conversation CLEARLY reveals expertise, role, or intent
- Use at most 1-2 tags per entity. Prefer ZERO tags if nothing specific is mentioned
- Use canonical forms: `expert:recommendation_system` not `expert:recommender_systems`
- If the entity's existing tags are provided, do NOT generate synonyms or variations — only add genuinely NEW information
- Prefer broad domains over sub-domains: `expert:ML` not `expert:deep_learning` + `expert:neural_networks`

Deduplication & Naming Rules (IMPORTANT):
- Use the entity's CANONICAL name (e.g., "千里眼" not "千里眼agent" or "千里眼兄弟")
- Do NOT extract Matrix IDs (e.g., @username:server.org) as separate entities — they refer to an already-named entity
- Do NOT extract pronouns ("他", "她", "they", "him") or vague references ("兄弟", "那个人", "the other agent") as entities
- If the same entity is referred to by multiple names/aliases, output it ONCE using the most recognizable name
- Do NOT extract the agent itself (the one generating the response) as an entity

Examples of what to extract:
- "My colleague Bob is a frontend expert" -> Bob (user, tags: ["expert:frontend"])
- "We use products from Google" -> Google (organization, tags: [])
- "Agent Research-01 helped me" -> Research-01 (agent, tags: [])
- "Talk to Alice about this" -> Alice (user, tags: [])

Examples of what NOT to extract:
- "@bob:matrix.org said hello" -> Do NOT extract "@bob:matrix.org" (use "Bob" if identifiable)
- "他很厉害" -> Do NOT extract "他" (pronoun)
- "那个兄弟 agent" -> Do NOT extract (vague reference)

If the conversation is purely between the primary speaker and the agent with no mention of anyone else, return an empty entities list."""
