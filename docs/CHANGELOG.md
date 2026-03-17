# Changelog

All notable changes to this project will be documented in this file.

---

## [0.2.0] — 2026-03-13

**170 files changed, +16,593 / -8,799 lines**

### Highlights

- **Matrix Communication Module** — Agents can now talk to each other via the Matrix protocol. Create rooms, send messages, discover agents, and coordinate in group chats — all through natural language.
- **ClawHub Skill Marketplace** — Chat-based skill installation: describe what you need, and the system recommends and installs matching skills from ClawHub.
- **LLM Cost Tracking** — Every LLM and embedding call is now metered. View cost breakdowns by model in the new Cost Popover UI.
- **Desktop App** — Electron-based desktop application with auto-updater and CI/CD release pipeline.

---

### New Features

#### Matrix Communication Module (`matrix_module/`)
Agents gain the ability to communicate with each other via a self-hosted Matrix homeserver (Synapse).

- **MCP Tools**: `matrix_send_message`, `matrix_create_room`, `matrix_join_room`, `matrix_invite_to_room`, `matrix_list_rooms`, `matrix_get_room_members`, `matrix_search_agents`, `matrix_get_agent_profile`, `matrix_register`
- **MatrixTrigger**: Background polling service (1 Poller + N Worker coroutines) that watches all registered agents for incoming messages, with adaptive polling intervals (5s active → 60s idle)
- **@Mention filtering**: In group chats, only agents explicitly mentioned via `m.mentions` are activated. Supports `@agent_id` for specific agents and `@everyone` for room-wide broadcast. Room creators are always activated regardless of mentions. DM rooms bypass filtering entirely.
- **Auto-registration**: Agents are automatically registered on the Matrix server when the module loads, with credential persistence in MySQL
- **NexusMatrix integration**: Deployed via Docker alongside Synapse homeserver (`deploy/synapse/`), with auto-clone and auto-update of the NexusMatrix service repo on desktop launch
- **Channel abstraction layer** (`channel/`): `ChannelContextBuilderBase`, `ChannelSenderRegistry`, `ChannelTag` — shared infrastructure for multi-channel message routing, reusable by future channel modules

| New files | Purpose |
|-----------|---------|
| `matrix_module.py` | Module registration, MCP config, system prompt instructions |
| `matrix_trigger.py` | Background message polling & dispatch |
| `matrix_client.py` | HTTP client for NexusMatrix API |
| `_matrix_mcp_tools.py` | MCP tool definitions |
| `_matrix_credential_manager.py` | Credential CRUD & auto-registration |
| `_matrix_hooks.py` | `hook_data_gathering` / `hook_after_event_execution` |
| `matrix_context_builder.py` | Channel-specific prompt construction |
| `contact_card.py` | Agent contact card for social network integration |

#### ClawHub Skill Installation
Chat-based skill discovery and installation from the ClawHub marketplace.

- **Chat install flow**: Users describe their needs in natural language → backend LLM searches ClawHub registry → recommends matching skills → one-click install
- **Environment config management**: Skills can declare required environment variables (e.g., API keys). Users configure them via a dedicated UI dialog before enabling the skill.
- **Runtime env injection**: Configured env vars are injected into the Claude agent subprocess at startup

| New/Modified | What changed |
|-------------|--------------|
| `backend/routes/skills.py` | New endpoints: `POST /skills/study`, `GET/PUT /skills/{id}/env-config` |
| `skill_module.py` | ClawHub search logic, env config storage & injection |
| `SkillsPanel.tsx` | Chat install UI, env config dialog |
| `schema/skill_schema.py` | `SkillEnvConfig` model |

#### LLM Cost Tracking
Every API call to Claude, OpenAI, and Gemini is now tracked with token counts and dollar costs.

- **Cost calculator** (`utils/cost_tracker.py`): Price table for OpenAI/Gemini/Embedding models; Claude costs use SDK-reported `total_cost_usd` directly
- **Global cost context**: `contextvars.ContextVar` enables automatic cost recording without passing `db`/`agent_id` through every call site
- **Database**: `cost_records` table with indexes on `agent_id` and `created_at`
- **Backend API**: `GET /api/agents/{agent_id}/costs?days=7` — returns summary (total cost, by-model breakdown, daily trend) and recent records
- **Frontend**: `CostPopover` component in the top bar showing today's spend, with expandable breakdown by model

| Integration point | How costs are captured |
|-------------------|----------------------|
| Claude Agent Loop | `sdk_cost_usd` from `ResultMessage.total_cost_usd` |
| OpenAI `llm_function()` | Token counts from `result.raw_responses[].usage` |
| Gemini API | `response.usage_metadata.{prompt,candidates}_token_count` |
| Embeddings | Token count from embedding response |

#### Desktop Application
Electron-based desktop app with full service orchestration.

- **Auto-updater**: Checks GitHub Releases for new versions, shows update banner, downloads and installs automatically
- **Service launcher**: Orchestrates Docker, backend, frontend, MCP servers, Matrix trigger, and NexusMatrix from a single process
- **CI/CD**: GitHub Actions workflow for automated desktop builds and releases (`desktop-release.yml`)
- **External links**: Opens GitHub, docs, and other links in the system browser

---

### Architecture Improvements

#### Backend Route Splitting
`backend/routes/agents.py` (1,850 lines) → 6 domain-focused sub-modules + 30-line aggregator:

| New module | Responsibility |
|-----------|---------------|
| `agents_awareness.py` | Awareness CRUD |
| `agents_chat_history.py` | Chat history queries |
| `agents_cost.py` | Cost tracking API |
| `agents_files.py` | Workspace file management |
| `agents_mcps.py` | MCP server management |
| `agents_rag.py` | RAG knowledge base operations |
| `agents_social_network.py` | Social network entity management |

#### Module Code Splitting
Large module files split into focused private sub-modules:

| Module | Before | After | Extracted |
|--------|--------|-------|-----------|
| `JobModule` | 2,334 lines | 591 lines | `_job_mcp_tools.py`, `_job_analysis.py`, `_job_lifecycle.py`, `_job_scheduling.py`, `_job_context_builder.py`, `prompts.py` |
| `SocialNetworkModule` | 1,601 lines | 423 lines | `_social_mcp_tools.py`, `_entity_updater.py` |
| `ChatModule` | 660 lines | 367 lines | `_chat_mcp_tools.py` |
| `GeminiRagModule` | 631 lines | 326 lines | `_rag_mcp_tools.py` |
| `NarrativeRetrieval` | 1,080 lines | 865 lines | `_retrieval_llm.py` |

#### Frontend Component Extraction

| New component | Extracted from | Purpose |
|-------------|---------------|---------|
| `AgentList.tsx` | `Sidebar.tsx` | Agent list with search, extracted to reduce Sidebar complexity |
| `EntityCard.tsx` | `AwarenessPanel.tsx` | Reusable social network entity display card |
| `KPICard.tsx` | `AwarenessPanel.tsx` + `JobsPanel.tsx` | Shared stats card component |
| `SkillCard.tsx` | `SkillsPanel.tsx` | Individual skill display with install/configure actions |
| `InstallDialog.tsx` | `SkillsPanel.tsx` | Skill installation confirmation dialog |
| `StatusDistributionBar.tsx` | `JobsPanel.tsx` | Job status distribution visualization |
| `useSkills.ts` | `SkillsPanel.tsx` | Skills data fetching hook (TanStack Query) |

---

### Performance Optimizations

- **Module decision skip** (`skip_module_decision_llm` setting): When enabled, bypasses the LLM instance decision call in Step 2 and loads all capability modules directly, saving ~2.5-3s per turn
- **Model override for LLM calls**: `llm_function()` now accepts an optional `model` parameter, allowing cheaper models (e.g., `gpt-4o-mini`) for routine judgment tasks like narrative matching
- **Narrative judge model config** (`NARRATIVE_JUDGE_LLM_MODEL`): Configurable model for narrative match/judge decisions, defaulting to `gpt-4o-mini`

---

### Infrastructure & DevOps

- **Synapse homeserver**: Self-hosted Matrix server deployed via Docker (`docker-compose.yaml`, `deploy/synapse/`)
- **CI pipeline**: `ci.yml` for automated checks
- **Desktop release pipeline**: `desktop-release.yml` for Electron builds
- **Makefile**: Common dev commands (`make dev`, `make test`, `make build`, etc.)
- **Backend config**: Centralized `backend/config.py` with environment-based CORS configuration
- **File safety**: `utils/file_safety.py` for filename sanitization and size enforcement

---

### Bug Fixes & Quality

- WebSocket send-after-close error handling
- Desktop packaging security improvements
- Inbox message grouping by Matrix room with agent identity resolution
- Frontend `ApiResponse` base type — eliminated 4 `as any` casts
- `preloadStore.ts` parallel data loading for faster initial load
- Path expansion (`~`) in settings via `model_validator`
- Duplicate `print` → `logger` migration in `module_runner.py`

---

### Database Changes

| Table | Action | Purpose |
|-------|--------|---------|
| `cost_records` | **New** | LLM API cost tracking |
| `matrix_credentials` | **New** | Agent Matrix account credentials |
| `matrix_agent_rooms` | **New** | Room-agent membership tracking |
| `inbox_table` | **Modified** | Added `source` JSON column for rich channel metadata |
