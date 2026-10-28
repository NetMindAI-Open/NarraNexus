---
code_file: frontend/src/components/chat/__tests__/AgentLlmConfigPanel.defaultFramework.test.tsx
last_verified: 2026-09-11
stub: false
---

# AgentLlmConfigPanel.defaultFramework.test.tsx

Pins where the per-agent editor's framework comes from when nothing is bound:
no owner slot → the framework `GET /api/providers/agent-framework` resolved
(mocked as `codex_cli` to prove it is not the old `'nexus_power'` literal); an
owner default framework still wins. api + configStore mocked.
