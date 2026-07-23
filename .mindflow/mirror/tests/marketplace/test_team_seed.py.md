---
code_file: tests/marketplace/test_team_seed.py
last_verified: 2026-07-22
stub: false
---

# test_team_seed.py

team seed 的 sha256 verify-then-store 闸门 + 幂等(此前零覆盖,唯一走网络组件)。httpx.AsyncClient stub。
