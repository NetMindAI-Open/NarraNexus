---
code_file: frontend/src/components/chat/__tests__/chatPanelAgentConfigEntry.test.tsx
last_verified: 2026-09-11
stub: false
---

# chatPanelAgentConfigEntry.test.tsx

Wiring guard for the Owner-required chat-header Model & framework entry:
owner → header button opens AgentLlmConfigPanel (stubbed) for the current agent;
the stub's save bumps the composer model chip's `reloadKey`; a viewer who does
not own the agent gets no button. Real ChatHeader + ChatPanel, api/hooks mocked.
