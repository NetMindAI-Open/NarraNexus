---
code_file: src/xyz_agent_context/agent_framework/quota_service.py
stub: false
last_verified: 2026-04-16
---

# Intent

Business orchestration layer above QuotaRepository. Every method honours
`SystemProviderService.is_enabled()`, so callers never need to guard:
disabled feature = consistent no-op contract.

## Upstream
- ProviderResolver — `check()` before routing to the system key branch
- cost_tracker.record_cost — `QuotaService.default().deduct()` post-call
  when `provider_source == "system"`
- backend/routes/auth.py /register — `init_for_user()` after successful
  cloud-mode registration
- backend/routes/quota.py /me — `get()` for user-facing budget view
- backend/routes/admin_quota.py — `grant()` / `init_for_user()` for staff

## Downstream
- QuotaRepository — all DB I/O
- SystemProviderService — `is_enabled()` gate + `get_initial_quota()`

## Disabled-state contract (is_enabled()==False)
| Method          | Behaviour    |
|-----------------|--------------|
| init_for_user   | returns None |
| check           | returns False|
| deduct          | silent no-op |
| get             | unchanged    |
| grant           | unchanged    |

`get` and `grant` bypass the gate intentionally: reading a row is always
safe, and staff should be able to credit users even if the feature is
temporarily disabled at the env level.

## Design decisions
- `deduct` and `init_for_user` swallow exceptions and log, rather than
  propagating. They run as side-effects of user requests; failures here
  must not break the user's LLM response or block registration.
- `grant` uses upsert semantics: when the target user has no row
  (pre-feature user) it creates one with `initial=0`, then applies the
  grant. Staff doesn't need to call init first.
- `default()` / `set_default()` classmethod pair exists so
  `cost_tracker.record_cost` — which runs far below the dependency
  injection boundary — can reach the live instance without threading
  it through every caller.

## Gotchas
- `init_for_user` is idempotent: re-seeding never overwrites prior usage.
  Re-registering the same user_id (should never happen normally, but
  possible with test harnesses) returns the existing row unchanged.
- `check` returns False on DB error, not True. If the DB is down we
  conservatively deny — the user sees 402 instead of accidentally
  consuming the system key beyond budget.
- The `default` singleton is process-local. Each backend process
  (backend / mcp / poller / jobs / bus) must call `set_default` in its
  own lifespan if it emits LLM cost events. If it doesn't, the hook is
  a silent no-op — safe fallback, not a crash.
