---
code_file: backend/routes/agents/capabilities.py
last_verified: 2026-09-07
stub: false
---

# routes/agents/capabilities.py — the owner's capability switches

## Intent

GET lists every registered module with its declaration, state (enabled / default / explicit / locked) and the context budget; PUT flips one module (`{"enabled": bool}`, base modules 400); DELETE returns a module to the default rule. Owner-gated exactly like `llm_config` (404 unknown agent, 403 not the owner). Mounted by `agents/core.py`; the PUT body is capped in `middleware/body_size.py`. Frontend: `components/chat/AgentCapabilitiesPanel.tsx`.

## 2026-09-07 — ownership through backend.routes._ownership

_require_owner delegates to assert_owned — the canonical helper (404 unknown / 403 not owner / local mode no-op) — instead of an eleventh hand-rolled copy that also disagreed on the local-mode posture. The unused _SAFE_ID_PATTERN is gone.
