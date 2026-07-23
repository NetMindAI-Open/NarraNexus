---
code_file: scripts/publish_batch.py
last_verified: 2026-07-22
stub: false
---

# scripts/publish_batch.py

Ops CLI: publishes every *.zip in a directory to a marketplace registry —
a loop over the same POST /api/marketplace/skills/publish that
publish_skill.py drives for one skill. Per-package outcome
(published / already-present / REJECTED-by-scan / error), exit 0 all-ok,
1 any-rejected, 2 transport error. Used to load the dev registry with the
batch of third-party skills for INTERNAL testing (packages live outside the
repo). Licensing caveat in the docstring: third-party skills without a clear
permissive license must NOT be rehosted in a PUBLIC marketplace — prefer
index-and-install-from-source (path B) there.
