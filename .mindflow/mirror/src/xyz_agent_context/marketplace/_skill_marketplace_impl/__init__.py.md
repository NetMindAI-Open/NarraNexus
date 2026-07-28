---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/__init__.py
last_verified: 2026-07-20
stub: false
---

# _skill_marketplace_impl/__init__.py

Private implementation package for the Skill Marketplace (underscore prefix =
never re-exported, per the layering rules). Planned residents per spec §9:
`registry.py`, `install_pipeline.py`, `artifact_store.py`, `secret_box.py`,
`scanner/`. Public access goes through `skill_marketplace_service.py` once
that lands; `SecretBox` is additionally consumed by SkillModule's env-config
path.

