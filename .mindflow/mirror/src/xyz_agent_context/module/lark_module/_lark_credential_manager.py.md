---
code_file: src/xyz_agent_context/module/lark_module/_lark_credential_manager.py
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — set_is_active (activation without re-bind)

Added `set_is_active(agent_id, is_active)`, mirroring the other channels' `set_enabled`. Flipping `is_active` → True is what makes the trigger's credential watcher pick up a bundle-imported (inactive) Lark credential and claim the app's single WS slot. Called by `POST /api/lark/set-active`.

# _lark_credential_manager.py — CRUD for lark_credentials table

## Why it exists

Per-agent Lark/Feishu bot binding state. Holds App ID + Secret
reference (Secret itself lives both in lark-cli's Keychain for CLI
tools, AND base64-encoded in DB for the SDK trigger that can't read
the Keychain). Centralises auth_status state-machine.

## auth_status state machine

```
not_logged_in   — DB row exists but credentials never verified
bot_ready       — `auth status` succeeded; trigger can subscribe
user_logged_in  — bot_ready + user OAuth complete (search features)
expired         — credential validation failed; needs re-bind
brand_mismatch  — runtime-detected (WS error 1000040351) wrong platform
```

`AUTH_STATUSES_BOT_ACTIVE = {bot_ready, user_logged_in}` is the
allowlist the trigger watcher uses to decide whether to start /
restart a subscriber for a credential. **brand_mismatch is excluded
intentionally** — restarting the trigger only re-hits the domain
error in a hot loop. The user has to unbind + re-bind with the
correct brand to recover.

## 2026-05-27 — added AUTH_STATUS_BRAND_MISMATCH (B.1)

Detected at runtime by `lark_trigger` when the WebSocket subscriber
observes error `1000040351`. State stored so:
- The trigger watcher won't keep restarting the doomed subscriber.
- The frontend can render a clear "wrong platform — re-bind" card
  ([[LarkConfig]] State 5).
- The agent prompt knows about it ([[lark_module]] Auth guidance)
  and can tell users "you picked the wrong platform" when they
  complain about silent bot.

## Gotchas

- `app_secret_encoded` is base64, **not encryption**. Inline comment
  flags this; production env should plug in cryptography.fernet via
  `LARK_SECRET_ENCRYPTION_KEY`.
- `migrate_legacy_auth_status` is the one-shot migrator for pre-4-
  state DB rows (`logged_in` → `bot_ready`). Conservative downgrade
  — we can't tell from the old row whether user OAuth was completed.
