---
code_file: backend/routes/__init__.py
last_verified: 2026-09-04
stub: false
---

# backend/routes/__init__.py

## Intent

Package marker with three convenience re-exports (websocket, agents core, providers). `jobs` and `skills` were re-exported here until batch 3c.5; they are now `backend.routes` contributions of builtin.job / builtin.skills mounted by `backend/plugins_host`, and nothing imported the re-exports. `backend/main.py` imports every router explicitly.
