---
code_file: src/xyz_agent_context/module/lark_module/_lark_event_probe.py
last_verified: 2026-05-27
stub: false
---

# _lark_event_probe.py — 5-second WebSocket health probe for event delivery

## Why it exists

The bind flow used to declare success after `auth status` confirmed
credentials work. That tells us the API rejects-or-accepts the
secret. It does NOT tell us:

1. Event subscription is **enabled** in the developer console.
2. Subscription mode is **WebSocket** (not Webhook).
3. encrypt_key / verification_token match.
4. The selected platform (Feishu vs Lark) matches the app's brand
   — only enforced on WS connect via error 1000040351.

Result: a colleague forgot to enable Event Subscription, bound
successfully, bot never replied to anything, and they had no idea
why. This probe catches that case at bind time.

## Upstream / Downstream

- **Called by**: `_lark_service.do_bind`, after the scope check passes.
- **Calls**: `lark-cli event +subscribe` as a 5-second subprocess.
- **Reads**: `_lark_workspace.get_home_env(agent_id)` for HOME
  isolation so the right `config.json` / keychain is used.

## Strategy

1. Spawn `lark-cli event +subscribe --format compact` as a subprocess.
2. Wait up to `PROBE_TIMEOUT_SEC` (5s) for either:
   - Process exits → categorise the error
   - Process is still alive at timeout → HEALTHY (subscriber is happily
     connected and waiting for events)
3. Kill the subprocess cleanly and return a structured result.

We **never keep the subscriber alive** — that's the long-running
trigger's job (`lark_trigger.py`). The probe is intentionally
bounded so it can't hang the bind flow.

## Failure categories

- `brand_mismatch` — saw error code 1000040351. Blocking — rollback.
- `event_sub_disabled` — saw "event subscription" / "not enabled" /
  "subscribe_failed" / "websocket disabled" in stderr.
- `connect_failed` — saw "dial tcp" / "dns" / "network unreachable".
- `timeout` — process died right at 5s mark with no specific error.
- `other` — any other immediate exit.

Each kind has a curated `user_hint` that becomes the warning text on
the frontend (non-blocking failure) or the action_hint in the
structured error (blocking failure = brand_mismatch only).

## Gotchas

- **Probe timeout vs UX**: 5s was the Owner-picked tradeoff (3s too
  flaky on slow networks; 10s too long for users staring at a
  spinner). Don't increase casually.
- **`lark-cli event +subscribe` output detection** is heuristic
  (string-match on stderr). If a future lark-cli changes its error
  strings or moves them to stdout, update `_FAILURE_HINTS` keys and
  the `combined` substring tests.
- **Probe failure on tooling errors** (`lark-cli not found`, spawn
  fail) returns `probe_ran=False`, which `do_bind` treats as
  fail-open (warn but don't block). Same principle as
  `_lark_scope_validator`.
