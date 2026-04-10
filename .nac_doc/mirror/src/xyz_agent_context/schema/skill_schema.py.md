---
code_file: src/xyz_agent_context/schema/skill_schema.py
last_verified: 2026-04-10
stub: false
---

# skill_schema.py

## Why it exists

`SkillModule` allows agents to install and study external capability bundles from GitHub or local paths. Each installed Skill is a directory under the agent-user workspace containing a `SKILL.md` manifest and supporting files. `SkillInfo` is the parsed representation of one installed Skill — its metadata, study status, and environment requirements. The other models (`SkillListResponse`, `SkillOperationResponse`, etc.) are the API DTOs for skill management endpoints.

## Upstream / Downstream

`SkillModule` reads Skill directories from disk and produces `SkillInfo` objects. The skill API routes in `backend/routes/` receive these objects and return them wrapped in response models. The frontend skill panel reads `SkillListResponse` to render the installed skills list with study status and env configuration state.

`SkillInfo.study_status` and `study_result` are written back by the async study pipeline: when a user triggers study, `SkillModule` spawns an `AgentRuntime` execution that reads the skill files and writes a natural language summary into `study_result`.

## Design decisions

**Study status as a string field rather than an enum**: `"idle"`, `"studying"`, `"completed"`, `"failed"`. This is intentional — skills are filesystem-backed objects and their state is stored in a JSON sidecar or similar, not in a database with enum constraints. Keeping it a free string is simpler for filesystem-based persistence.

**`env_configured` never returns actual values** (per the docstring). The `SkillEnvConfigResponse.env_configured` dict maps env var name to `True/False` (is it set?) but never reveals the actual value. This prevents API endpoints from leaking secrets.

**`requires_env` and `requires_bins` detected from frontmatter and study**: the Skill manifest (`SKILL.md`) declares dependencies in YAML frontmatter. After study, the agent may also discover additional requirements. Both sources contribute to these fields.

## Gotchas

**`SkillInfo.path`** is the full filesystem path to the skill directory. It is machine-specific and cannot be shared across installations. If you serialize `SkillInfo` to JSON and deserialize it on another machine, `path` will be wrong.

**`study_result` is the agent's own natural language summary** of what the skill does, not the raw `SKILL.md` content. If the study fails (`study_status = "failed"`), `study_result` is `None` and `study_error` has the error. A failed study does not prevent the skill from being used — the agent will attempt to use it without the study summary.

## New-joiner traps

- `SkillInfo` has no `id` field. Skills are identified by `name` (the directory name, not a UUID). The name must be unique within a given agent-user workspace, but two different agents can have skills with the same name.
- `AgentSkill` in `a2a_schema.py` and `SkillInfo` in this file are entirely different concepts despite the similar naming. `AgentSkill` is an A2A protocol capability declaration for external agents. `SkillInfo` is an installed tool bundle for the current agent's use.
