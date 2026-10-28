---
code_file: plugins/builtin.skills/src/narranexus_plugins/skill_module/services.py
last_verified: 2026-09-04
stub: false
---

# skill_module/services.py — builtin.skills services

## Intent

Exposes `skills.workspaces`: `(agent_id, user_id) -> SkillModule` behind the `contracts.skill.SkillWorkspace` Protocol. The bundle importer, the marketplace service / install pipeline / registry and the skill sync service obtain workspaces through it.
