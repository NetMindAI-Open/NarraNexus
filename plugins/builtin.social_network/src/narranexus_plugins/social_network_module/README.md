# Social Network Module

## Overview

Social Network Module provides **social network recording and search** capabilities for Agents. It enables Agents to:
1. Record information about entities (users/agents) they interact with
2. Intelligently search contacts in the social network
3. Automatically load relevant information during conversations

## Phase 1 MVP Features

### Implemented

1. **Database Table Structure** (`social_network_entities`)
   - Stores entity identity info, contact details, expertise areas, etc.
   - Data isolated by `owner_agent_id`

2. **MCP Tools**
   - `extract_entity_info` - Extract and update entity information
   - `recall_entity` - Recall information about a specific entity
   - `search_social_network` - Search the social network (by expertise area)
   - `get_contact_info` - Get contact information

3. **Hook: gather**
   - Automatically loads current interaction entity info when building context
   - Lets the Agent know "what I already know about the other party"

4. **Basic Search**
   - Exact lookup by `entity_id`
   - Fuzzy search by `expertise_domains`

### Pending (Phase 2)

- `after_turn` - Automatic post-event summarization
- Semantic search (using vector embeddings)
- Intent recognition (during data-gathering phase)
- Automatic relationship strength calculation
- Public expert directory integration

---

## Quick Start

### 1. Create Database Tables

```bash
# Preview changes (dry-run)
uv run python src/narranexus/platform/utils/sync_all_tables.py --tables social_network_entities --dry-run

# Actually create tables
uv run python src/narranexus/platform/utils/sync_all_tables.py --tables social_network_entities
```

### 2. Test Database Operations

```bash
# Run test
uv run python src/narranexus/platform/utils/database_table_management/sync_all_tables.py --tables instance_social_entities --dry-run
```

### 3. Enable Module in Agent

Modify Step 2 in `agent_runtime.py`:

```python
module_selected_list = [
    "AwarenessModule",
    "ChatModule",
    "BasicInfoModule",
    "SocialNetworkModule"  # Add this line
]
```

---

## Database Table Structure

### `social_network_entities` Table

| Field | Type | Description |
|-------|------|-------------|
| `id` | BIGINT | Auto-increment primary key |
| `entity_id` | VARCHAR(64) | Entity ID (user_id or agent_id) |
| `entity_type` | VARCHAR(32) | Entity type (user/agent/expert) |
| `owner_agent_id` | VARCHAR(64) | Owning Agent ID (data isolation) |
| `entity_name` | VARCHAR(255) | Entity name/nickname |
| `entity_description` | TEXT | Entity description |
| `identity_info` | JSON | Identity info (organization, position, expertise, preferences) |
| `contact_info` | JSON | Contact info (chat_channel, email, preferred_method) |
| `relationship_strength` | FLOAT | Relationship strength (0.0-1.0) |
| `interaction_count` | INT | Interaction count |
| `last_interaction_time` | DATETIME | Last interaction time |
| `tags` | JSON | Tag list |
| `expertise_domains` | JSON | Expertise domain list |
| `created_at` | DATETIME | Creation time |
| `updated_at` | DATETIME | Update time |

---

## MCP Tools Usage

### 1. extract_entity_info

**Purpose:** Extract and update entity information

**Parameters:**
```json
{
  "entity_id": "user_alice_123",
  "updates": {
    "entity_name": "Alice",
    "entity_description": "Algorithm Lead",
    "identity_info": {
      "organization": "Acme Corp",
      "position": "Algorithm Lead",
      "expertise": ["recommendation systems", "machine learning"]
    },
    "contact_info": {
      "chat_channel": "xyz_chat_12345"
    },
    "expertise_domains": ["recommendation systems", "machine learning"],
    "tags": ["colleague", "expert:recommendation systems"]
  },
  "update_mode": "merge"  // or "replace"
}
```

**When to call:**
- When a user introduces themselves
- When a user provides new identity information
- When you learn about a user's expertise area

### 2. recall_entity

**Purpose:** Recall information about a specific entity

**Parameters:**
```json
{
  "entity_id": "user_alice_123"
}
```

**Returns:**
```json
{
  "success": true,
  "entity": {
    "entity_id": "user_alice_123",
    "entity_name": "Alice",
    "identity_info": {...},
    "expertise_domains": [...],
    ...
  }
}
```

**When to call:**
- At the start of a conversation (load current interaction entity info)
- When other people are mentioned in conversation

### 3. search_social_network

**Purpose:** Search the social network

**Parameters:**
```json
{
  "query": "recommendation systems",
  "search_type": "expertise",  // expertise | semantic | tags
  "top_k": 5
}
```

**Returns:**
```json
{
  "success": true,
  "results": [
    {
      "entity_id": "user_alice_123",
      "entity_name": "Alice",
      "expertise_domains": ["recommendation systems", "machine learning"],
      ...
    }
  ],
  "count": 1
}
```

**When to call:**
- When a user asks "Who knows about XX?"
- When the Agent needs expert help
- When an expert referral is needed

### 4. get_contact_info

**Purpose:** Get contact information

**Parameters:**
```json
{
  "entity_id": "user_alice_123"
}
```

**Returns:**
```json
{
  "success": true,
  "entity_id": "user_alice_123",
  "entity_name": "Alice",
  "contact_info": {
    "chat_channel": "xyz_chat_12345",
    "preferred_method": "chat"
  }
}
```

---

## Code Examples

### Using in Other Modules

```python
from narranexus.platform.repository.instance_social_entities_repository import InstanceSocialEntitiesRepository

# Use Repository for CRUD operations
repo = InstanceSocialEntitiesRepository(db_client)

# Get entity
entity = await repo.get_by_id("entity_123")

# Query by condition
entities = await repo.get_by_instance_id("inst_abc123")
```

---

## Design Philosophy

### 1. Clear Responsibilities
- Social Network Module **only handles recording and search**
- Does not involve specific communication implementation (handled by other modules)
- Provides an interface for communication modules via the `contact_info` field

### 2. Data Isolation
- Each Agent has an independent social network
- Strictly isolated by `owner_agent_id`
- No data sharing between different Agents

### 3. Global Perspective
- Phase 1 does not associate with Narratives
- Entity information is shared across all of an Agent's Narratives
- Avoids duplicate storage, maintains unified cognition

### 4. Progressive Enhancement
- Phase 1 implements core CRUD and basic search
- Phase 2 adds semantic search, automatic summarization, and other advanced features
- Architecture supports future extensions (e.g., adding `social_network_narrative_contexts` table)

---

## File Structure

```
social_network_module/
├── __init__.py                   # Module exports
├── social_network_module.py      # Main module class
├── mcp_server.py                 # MCP Server implementation
└── README.md                     # This document

utils/database_table_management/
├── create_instance_social_entities_table.py  # TableManager definition
└── sync_all_tables.py                        # Table sync tool
```

---

## Next Steps (Phase 2)

### 1. Automatic Summarization
- Implement `after_turn`
- Detect identity information in conversations
- Automatically call LLM for information extraction

### 2. Semantic Search
- Generate embeddings for entity information
- Store in database or vector database
- Implement natural language search

### 3. Intent Recognition
- Recognize in `gather`:
  - Whether other entities are mentioned
  - Whether expert search is needed
- Prompt Agent to proactively call tools

### 4. Relationship Strength Calculation
- Automatically update based on interaction frequency and depth
- Support relationship decay (reduced strength after prolonged inactivity)

### 5. Public Expert Directory
- Design public expert data structure
- Support global search in `search_social_network`
- Permission control and privacy protection

---

## FAQ

### Q1: Why not associate with Narratives?

**A:** Phase 1 adopts a global perspective where Entity information is shared across all Narratives. Reasons:
1. Simplifies architecture, speeds up development
2. Avoids data duplication
3. Aligns with the real-world logic that "perception of a person is unified"

If future needs require distinguishing "Alice at work" from "Alice in private", a `social_network_narrative_contexts` table can be added.

### Q2: How are information conflicts handled?

**A:** Currently uses a "newest information wins" strategy:
- In `update_mode="merge"`, new information overwrites old information
- Array fields (e.g., `expertise_domains`) are merged with deduplication
- Phase 2 will add timestamp and confidence fields

### Q3: How is search performance?

**A:** Phase 1 uses MySQL's JSON_SEARCH, suitable for small-scale data. Phase 2 will:
- Use vector database (Qdrant/Pinecone) for semantic search
- Add indexes to optimize query performance

### Q4: How is privacy protected?

**A:**
- Data is strictly isolated by `owner_agent_id`
- Only stores information actively shared by users
- Future plans include interfaces for users to view/delete their own information

---

## Contribution Guide

If you want to extend this module:

1. **Add new fields**: Modify the `SocialNetworkEntity` Pydantic model, then run `sync_all_tables.py`
2. **Add new tools**: Define tools in `mcp_server.py`, implement logic in `social_network_module.py`
3. **Add new search methods**: Extend the `_search_entities` method
4. **Add new hooks**: Implement in `social_network_module.py`

---

## License

Consistent with the main project repository.
