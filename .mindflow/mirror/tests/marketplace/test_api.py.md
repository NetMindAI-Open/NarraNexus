---
code_file: tests/marketplace/test_api.py
last_verified: 2026-07-21
stub: false
---

# test_api.py

Route tests for /api/marketplace/skills/* on a mini FastAPI app
(ASGITransport pattern from tests/backend/). Forces deployment mode to
"cloud" by monkeypatching the service module's get_deployment_mode, stubs
resolve_current_user_id, points db_factory.get_db_client at the fixture
client. Covers the publish token gate, 422 scan_report shape, search +
installed annotation, detail 404, download headers + counter increment,
install 200/409(SKILL_ALREADY_INSTALLED)/404, and both /updates modes
(agent_id vs batch skills= spec, plus 400 when neither).
