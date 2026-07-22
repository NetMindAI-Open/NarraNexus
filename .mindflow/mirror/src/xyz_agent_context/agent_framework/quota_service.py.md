---
code_file: src/xyz_agent_context/agent_framework/quota_service.py
stub: false
last_verified: 2026-07-18
---

## 2026-07-18 — set_preference / QuotaPreferenceLocked 删除；新增 rearm_switch_notice

用户偏好没了（免费额度优先=平台行为，见 [[provider_resolver]]）：
`set_preference` 及其"耗尽时禁止重开"的 `QuotaPreferenceLocked` 409 守卫
整体删除（唯一调用方 PATCH /me/preference 路由同日删）。新增
`rearm_switch_notice(user_id)`（无条件 repo.set_preference(uid, True)，
0→1 边沿由 resolver 守）。`disable_preference_if_enabled` 保留但语义改为
**通知闩锁的 CAS**（armed→fired），docstring 已改写；方法名沿用列名避免
无谓改名涟漪。

## 2026-07-07 — `disable_preference_if_enabled` (compare-and-swap, #48)

New method wrapping `QuotaRepository.disable_if_enabled`: turn the free-tier
preference OFF **only if currently ON**, returning True iff this call did the
1→0 transition. The provider resolver uses it so the #48 auto-switch flips the
preference and fires its one-time "switched to your own key" notice exactly
once under concurrent exhausted requests. Distinct from `set_preference`
(unconditional write, still used by the user-facing toggle).

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

## set_preference and QuotaPreferenceLocked (#48)

`set_preference(user_id, prefer)` persists `prefer_system_override` on the
quota row. It has one hard invariant: turning the free-tier preference ON
(`prefer=True`) while the quota has **no remaining budget** raises
`QuotaPreferenceLocked` (a new exception class in this module). Turning it
OFF (`prefer=False`) is always allowed — that path is what `classify` calls
automatically when quota is exhausted but the user has an own provider. The
free tier can only be re-enabled once the quota is replenished (grant or
next quota cycle). `QuotaPreferenceLocked` propagates to the route layer;
`quota.py` maps it to HTTP 409.

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
