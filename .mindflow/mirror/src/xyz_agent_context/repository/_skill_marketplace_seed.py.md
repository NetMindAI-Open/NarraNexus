---
code_file: src/xyz_agent_context/repository/_skill_marketplace_seed.py
last_verified: 2026-07-22
stub: false
---

# _skill_marketplace_seed.py

Publishes the repo-vendored first-party skills (marketplace_skills/) into the
registry host's catalog + store on lifespan — the skill-side parallel of
_team_marketplace_seed. Without it a fresh cloud deploy showed an empty
Skills tab AND default-skill install (NetMind vision/audio) found nothing to
install. Scope is FIRST-PARTY only (dirs physically under marketplace_skills/,
i.e. the two NetMind default skills); third-party clawhub skills are NOT
seeded — they go through scripts/publish_skill.py deliberately (license/
attribution). Reuses RegistryService.publish (extract -> scan gate -> store ->
catalog, is_default from manifest). Idempotent (skip when (id,version) already
catalogued + blob in store), best-effort per skill, registry-host only. Path:
MARKETPLACE_SKILLS_DIR override, else parents[3]/marketplace_skills; None ->
no-op (pip-installed package without the dir).
