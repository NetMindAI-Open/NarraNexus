---
code_file: backend/onboarding/personas.py
last_verified: 2026-08-19
stub: false
---

# personas.py — content pools + renderers for the guide agent

## Why it exists

The guide agent's charm is randomized: 6 personas × 6 topic openers ×
13,824 names means two new users almost never meet the same companion. This
file is the single home for that scenario content (铁律 #4: it lands in
Awareness / bootstrap state, never in generic prompts) — pure data and string
rendering, no IO, so it is trivially testable and reviewable as copy.

## Upstream / Downstream

**Consumed by:** `profile.py` (greeting / Bootstrap.md renderers via
ctx.extra) and `provisioning.py` (random picks + awareness rendering at
provision time).

## Design decisions

- **The greeting is bilingual (EN + 中文) by design.** It renders at provision
  time, when the user's UI language is unknown: netmind-login carries no
  locale, and the frontend's `localizeBootstrapGreeting` only auto-translates
  the system-default greeting — scenario-authored greetings pass through
  verbatim. Once the user speaks, the awareness LANGUAGE rule makes the agent
  mirror them.
- **Awareness carries a missing-handbook fallback**: skill install is
  best-effort (offline desktop installs can fail to fetch narranexus-guide),
  so the HOW TO TEACH section tells the agent what to do when SKILL.md is
  absent — admit the handbook isn't installed, never invent UI steps.
- **The local-install provider notice is a render flag** (`is_local`), not a
  separate template: local installs cannot reply until a model provider is
  configured, so both the greeting (point 4) and the awareness (LOCAL INSTALL
  NOTE) explain that — the one thing the agent itself can never explain while
  it has no working provider.
- **Proactive discipline lives in awareness AND in the job payload** (see
  provisioning.py): the 3-ignored-check-ins goodbye and the self-pause are
  agent-judged, so they are stated in both places the model reads — and both
  spell out the real tool contract (find the job's id with a retrieval tool,
  then job_update with agent_id + job_id + status='paused'; the bare
  job_update(status=...) shorthand would fail on required params).

## Gotchas

- `persona_by_key` falls back to PERSONAS[0] — a deleted/renamed persona key
  in an old agent's metadata must never break greeting re-renders.
- Topic/persona text is user-facing copy; changing it changes what NEW agents
  render, but existing agents keep their provision-time snapshot (greeting is
  render-then-store, see bootstrap/profiles.py).
