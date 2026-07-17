# Feedback Mechanism — Design (Phase 1: collection only)

Date: 2026-07-10 · Branch: `feat/user-feedback` · Status: approved by Bin (chat)

## Goal

Agents and users report dissatisfaction/problems; everything lands deduplicated
in one database owned by the team. Phase 1 is **collection only** — no Lark
notification, no admin UI, no auto bug-Base writes.

## Decisions (locked with Bin)

1. **Detection**: agent self-awareness (MCP tool) as primary + explicit Web UI
   button as fallback. Two agent triggers: (a) user expresses dissatisfaction,
   (b) the agent itself fails the same user instruction repeatedly (≥2).
2. **Payload**: one-line summary + category + severity + metadata ONLY. Never
   raw conversation text, never PII. user_id/agent_id are hashed client-side.
3. **Transport**: hardcoded HTTPS URL, identical for local/cloud/self-hosted:
   `https://agent.narra.nexus/feedback/api/feedback`
   (path-routed under the app domain: no new DNS/cert; caddy `handle` placed
   before the maintenance-mode import so intake survives maintenance).
   Env override `NARRANEXUS_FEEDBACK_URL` exists for dev/test;
   `NARRANEXUS_FEEDBACK_DISABLED=1` is the kill switch (decision "B").
4. **Storage first**: everything goes into a DB table; Lark hookup is Phase 2.
5. **Dedup**: raw reports kept; issues deduplicated by fingerprint with
   occurrence count + distinct-user set (hashed).

## Components

### 1. NarraNexus — `feedback_client` (`src/xyz_agent_context/services/feedback_client.py`)

`send_feedback(*, category, summary, severity, source, deployment, agent_hash,
user_hash, channel, app_version) -> bool` — fire-and-forget: 3 s timeout, one
attempt, all failures swallowed (log at DEBUG). Respects kill switch. Never
blocks the agent loop (called via `asyncio.create_task` from async contexts).

### 2. NarraNexus — MCP tool in `basic_info` module

Tool `submit_feedback(category, summary, severity)`;
categories: `user_dissatisfaction | repeated_failure | error | feature_gap | other`;
severity: `low | medium | high`. Prompt guidance added to basic_info
instructions: call when the user expresses dissatisfaction, or after failing
the same instruction twice; the summary must describe the PROBLEM, not quote
the user; no names/keys/conversation excerpts.

### 3. NarraNexus — backend route + Web UI

`POST /api/feedback` (authenticated) accepts `{category, text}` from the
frontend dialog, truncates text to 500 chars, forwards through
`feedback_client` with `source=web_ui`. Frontend: sidebar footer "Feedback"
button → dialog (category select + textarea) → toast on submit. i18n: en + zh
keys (other locales fall back to en).

### 4. deploy repo — `feedback-svc` (`stacks/feedback-svc/`)

Tiny FastAPI container, own sqlite volume (deliberately NOT the app DB — must
stay up when the app is down). Only `POST /api/feedback` exists; every other
path/method → 404/405. Guards: body ≤ 8 KB, strict schema, per-IP rate limit
(sliding 60 s window, default 30 req), silent drop on violation. Joins the
external `narranexus_app` network so ops-caddy can route to it by name.

Schema:

```sql
feedback_raw   (id, received_at, source, category, severity, summary,
                deployment, agent_hash, user_hash, channel, app_version,
                client_ip_hash, fingerprint)
feedback_issues(fingerprint PK, category, first_seen, last_seen,
                occurrences, distinct_users,   -- count
                sample_summary, user_hashes)   -- JSON array, hashed
```

Fingerprint = sha256(category + normalized summary) where normalization
lowercases and strips digits/hex-ids/paths, so "job 123 failed" and
"job 456 failed" collapse into one issue.

### 5. deploy repo — `log-sentry` nightly self-check

`scripts/log_sentry.sh` + systemd timer (03:10 nightly, prod + dev EC2): scans
last 24 h of `docker logs` for every `narranexus-*` container for
ERROR/Traceback/restart counts + disk/mem watermarks, aggregates locally by
the same normalization, POSTs each finding once (`source=log_scan`) to the
same endpoint.

## Out of scope (Phase 2+)

Lark digest/instant push, admin/query UI, auto bug-Base records, attaching
conversation excerpts with consent, feedback client in the Tauri shell.

## Test plan

- unit: client (kill switch, timeout, payload shape), svc (schema reject,
  rate limit, dedup math incl. distinct_users), backend route (auth, truncation).
- e2e: run svc locally → send via client + via backend route + simulated MCP
  call → assert `feedback_raw`/`feedback_issues` rows, dedup counters, and
  distinct-user counting; then live smoke against prod after deploy.
