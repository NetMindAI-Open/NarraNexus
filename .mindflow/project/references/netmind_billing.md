# NetMind billing: key ↔ account ↔ top-up, and how balance-insufficient is handled

**When to read**: touching NetMind key minting/provisioning, the "insufficient
balance/quota" failure path, or the Job-layer no-quota pause; wondering why we
can't pre-check balance or auto-detect a top-up.

## The key → account → balance chain (and where it used to break)

1. **Login** → the frontend holds a NetMind login JWT. `netmind_auth_client.verify_token(jwt)`
   → `NetmindUser(user_system_code, email)` — `user_system_code` is the NetMind
   **account** id. This is the ONLY place account identity is exposed.
2. **Mint** → `netmind_key_client.create_key(jwt)` → `MintedKey(apitoken, token_id)`.
   The minted key is **opaque** — it carries no account id.
3. **Store** → `netmind_provisioner.ensure_netmind_provider` calls
   `UserProviderService.onboard_one_key`, writing the key to `user_providers`
   (one user can have several NetMind providers; a NetMind onboard may create a
   dual `linked_group` of anthropic+openai rows).

Historically step 2/3 dropped the account identity, so given a stored key we
could not tell **which** NetMind account it belonged to. The incident: a user
made several keys from ONE broke account, couldn't tell them apart in the UI,
and topped up a different account — still broke.

**Fix (2026-07)**: at mint time (we still hold the JWT), `ensure_netmind_provider`
`verify_token`s and stamps `user_providers.netmind_account_id` +
`netmind_account_email` on the user's NetMind rows (additive, nullable columns).
`GET /api/providers` attaches the email per provider, and Settings → Providers
displays "NetMind account: <email>" so the user tops up the RIGHT account.
Capture is best-effort — a failure never fails provisioning (account stays NULL).
**Existing users are reached via the early-return (dedup) path**: on their next
login, `ensure_netmind_provider` sees the existing row, and if
`netmind_account_id` is NULL it backfills it (without minting a new key) — so the
people who ALREADY have a key (incl. the incident users) get labelled too, not
only brand-new ones.

## Why there is no pre-call balance check

`get_fee_info` (balance) requires the **login JWT**, which the backend does NOT
persist (it lives on the frontend, by design — we store the minted key, never
the JWT). So we cannot check balance before a run, nor poll for a top-up. Only
non-secret account id/email is stored. Detection is therefore **reactive** — the
provider returns the billing error at call time.

Propagation: a NetMind top-up can take **~5 minutes** to take effect (operational
fact from the incident; not code-derivable). The user-facing copy says so.

## How a balance/quota-insufficient failure is handled

Detection is provider-agnostic via `llm_failure.classify_self_serviceable`
(#110): `insufficient balance / insufficient_quota / insufficient funds /
insufficient credit / not enough balance / exceeded your current quota /
402 payment` (also context-window and model-not-found). A bare `429` / rate-limit
is NOT billing — it stays transient.

Two layers, deliberately different (see the two mirror mds for detail):

- **Real-time layer** (`agent_circuit_breaker` + `response_processor` /
  `step_3_agent_loop`): **does NOT pause** on a self-serviceable failure. It
  surfaces an actionable per-turn message and returns — an interactive user reads
  it and acts, and pausing would only block their corrected retry after they top
  up (binding rule #14/#15). This is #110's shipped decision; we keep it.
- **Job layer** (`job_trigger._is_no_quota_failure`): **pauses** — a background
  job has no interactive reader, so the only way to stop the retry storm (the
  9-user / 14-day / 390-retry incident) is `PAUSED_NO_QUOTA`. It reuses the same
  `classify_self_serviceable` (single source of truth) and records a
  `paused_reason`.

  **Recovery is reason-split — this is the crux.** The time-based backstop
  (`_resume_eligible_no_quota_jobs`) resumes on a readiness check
  (`_user_can_run` = config completeness), which **cannot observe a balance
  top-up** (topping up leaves the config unchanged; we can't pre-check balance —
  no stored JWT). So a balance-0 user reads as "config complete → can run", and
  blindly re-arming that job every cycle IS the storm (re-arm → re-fail →
  re-pause, forever). Therefore the backstop **skips**
  `_EDGE_ONLY_RESUME_REASONS` (`insufficient_balance` / `context_window` /
  `model_not_found`) — their fix is only visible as a config change. Those
  resume **only on a real edge**: a provider/slot reconfigure fires
  `rearm_user_no_quota_jobs` (which resumes ALL reasons + clears
  `paused_reason`), or a manual action. Auth / legacy-quota pauses are NOT in the
  skip set — reconfiguring a key DOES change config, which readiness observes, so
  they keep the existing time-based recovery.

  **How a top-up IS detected:** the edge path (`rearm_user_no_quota_jobs`, fired
  on every login + on provider save) runs `ProviderReadiness.validate`, which for
  a NetMind user does a LIVE `/chat/completions`. `_interpret_test_response` was
  fixed (PR #116 review) so a self-serviceable 400/404 body (NetMind's
  `balance not enough`, model-not-found, context) is reported as **not ready**
  (via the shared `classify_self_serviceable`) instead of the old
  "400 = auth passed = reachable". So: still-broke → live test says not-ready →
  job stays paused (NO wasted run); after a top-up → live test 200 → resumed. Net
  recovery = **auto, on the next login after top-up** (or on provider save), and
  accurate. This is a genuine stop + accurate recovery — not a blind periodic
  probe (an earlier draft's ~15-min-probe framing was wrong; it would have
  replaced dev's "8 tries then FAILED" with unbounded re-arming).

  Onboarding is unaffected by the `_interpret_test_response` change — it only
  hard-rejects on auth phrases (401/403/"invalid api key"); a balance-dead key
  still onboards "unverified". The "test connection" button now shows the real
  cause ("insufficient balance") instead of a misleading "OK".
