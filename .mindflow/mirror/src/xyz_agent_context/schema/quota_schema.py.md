---
code_file: src/xyz_agent_context/schema/quota_schema.py
stub: false
last_verified: 2026-04-16
---

# Intent

Pydantic model for per-user system-key free tier quota. Lives here (not inside
`provider_schema.py`) because quota semantics are orthogonal to provider/slot
config: quota changes on every LLM call, providers change rarely.

## Upstream
- Cloud-mode `/api/auth/register` (creates a row via QuotaService.init_for_user)
- Staff admin endpoints (`grant` / `init`)
- ProviderResolver (reads `has_budget()` before routing to system key)

## Downstream
- QuotaRepository: DB I/O for `user_quotas` table

## Design decisions
- Input and output tokens tracked separately — NetMind pricing diverges 5x+.
- `granted_*` is additive and distinct from `initial_*` so the original
  signup allocation stays auditable after staff top-ups.
- `remaining_*` clamps at 0 via `max(0, ...)` to make "slightly overdrawn due
  to concurrent requests" visible only to staff/log, not to the user.
- No `reset_at` — MVP is one-shot + manual grant. Future periodic reset adds
  the field without breaking this schema.

## Gotchas
- Do NOT derive `status` automatically from `remaining_*`. Staff may
  `DISABLED` a user regardless of remaining budget.
