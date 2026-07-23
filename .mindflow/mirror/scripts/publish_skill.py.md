---
code_file: scripts/publish_skill.py
last_verified: 2026-07-21
stub: false
---

# scripts/publish_skill.py

Ops CLI for publishing skills to the marketplace registry via
`POST /api/marketplace/skills/publish` (token-gated). Accepts a .zip or a
skill directory (zipped on the fly, `.skill_meta.json` excluded — install
bookkeeping must not ship). Exit code 1 on a security-gate rejection with
the full rule/file/line report printed — usable in CI. This is the tool
stage ⑧ uses to publish the first MVP skills once the skill list and the
S3 bucket are settled.
