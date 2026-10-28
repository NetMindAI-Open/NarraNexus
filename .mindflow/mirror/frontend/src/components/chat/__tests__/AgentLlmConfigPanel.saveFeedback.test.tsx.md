---
code_file: frontend/src/components/chat/__tests__/AgentLlmConfigPanel.saveFeedback.test.tsx
last_verified: 2026-09-11
stub: false
---

# AgentLlmConfigPanel.saveFeedback.test.tsx

Pins the per-agent LLM config modal's save feedback (GitHub #96) with `api`
and `configStore` mocked: a successful save shows "✓ Saved" after the Save
button; a failed save shows the error and no confirmation; a helper failure
after the agent slot saved names the half that landed and keeps the helper
edit. Also pins that thinking / reasoning effort are disabled until the
agent slot has a provider (located by their bound labels).
