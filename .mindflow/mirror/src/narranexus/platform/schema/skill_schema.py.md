---
code_file: src/narranexus/platform/schema/skill_schema.py
last_verified: 2026-08-13
stub: false
---

## 2026-07-21 — SkillInfo.source_type(stage 7)

新增 `source_type`(marketplace|url|github|zip|builtin|manual),由
`_parse_skill_md` 从 `.skill_meta.json` 回填;前端 SkillCard 的来源徽标
消费它。纯 additive。


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

## 2026-08-13 — `env_platform_assumed` 字段

新增 `env_platform_assumed: Optional[list[str]]`。语义:该 skill 的必填 env 里,**仅因平台可解析(`PLATFORM_RESOLVED_ENV`,如 `NETMIND_API_KEY`)才算已配置、且用户没有自存**的那些 var 名。由 [[skill_module]].env_config_status 在 `_parse_skill_md` 两个 return 处填。用途:API 层 `_enrich_platform_env_status` 只对这半边做 DB 校验并按需把 `env_configured` 降为 False——自存的平台 var 被排除在外,不会被误降(2026-08-13 反向假阴性修复)。**只放 var 名、绝不放值**(本对象是 `GET /api/skills` 响应体,原样发前端)。`None` = 无平台假设,enrich 不降级(其它 `SkillInfo` 生产者不填即默认安全)。

## 2026-07-10 — built-in 字段

- `builtin: bool = False` 标记随 app 出厂的内置技能（如 `officecli`）。由 `SkillModule._scan_skills`/`_parse_skill_md` 从 `.skill_meta.json` 的 `builtin` 键回填。语义：可 disable、**不可 remove**、不进用户备份/导出。详见 `skill_module/_overview.md`。
