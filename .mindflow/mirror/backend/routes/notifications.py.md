---
code_file: backend/routes/notifications.py
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — 三处身份 401 改带 `identity_unresolved`

`HTTPException(401, "Authentication required")` → `AuthError(IDENTITY_UNRESOLVED)`。
middleware 已经验证过凭证，这里 `request.state.user_id` 还是空的话属于接线
bug，不该触发全局登出。8/2 日志里的 `/api/notices` 401 就落在这一类的
可疑范围内。见 [[auth_errors]]。

# notifications.py — user-facing notification endpoints

Three endpoints under ``/api/notifications``:

* ``GET /me?unread_only=...&limit=...`` — newest first, returns
  ``{items, unread_count}``. ``unread_count`` is computed against the
  full unread set, independent of ``limit``, so the bell-icon badge
  stays accurate.
* ``POST /{id}/read`` — single-row mark-read. Silently no-ops on IDs
  that belong to another user (returns ``ok=False``) — never raises
  403 because that would leak existence of foreign IDs.
* ``POST /read-all`` — sweep every unread row for this user.

## First producer

The Provider Unification self-heal path
(``provider_driver.self_heal.self_heal_if_broken``) writes
``slot_auto_repaired`` rows. The payload is JSON text with
``slot_name`` / ``old_model`` / ``new_model`` / ``card_name`` /
``card_provider_id`` / ``reason``. Frontend parses payload by ``kind``.

## Design choices

Payload is opaque JSON text on the wire — frontend gets it parsed in
the handler so the type isn't ``str``. Severity is ``info`` /
``warning`` / ``error`` to drive UI colour. Authentication is via the
standard ``request.state.user_id`` middleware; the endpoint does NOT
appear in QUOTA_BYPASS_PREFIXES because users with no quota can still
have notifications worth reading.
