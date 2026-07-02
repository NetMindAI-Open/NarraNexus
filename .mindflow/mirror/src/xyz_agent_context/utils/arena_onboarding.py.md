---
code_file: src/xyz_agent_context/utils/arena_onboarding.py
last_verified: 2026-06-23
stub: false
---

# arena_onboarding.py — register an Agent on Arena + lay the skill into a workspace

## Why it exists

NetMind Agent Arena (`arena42.ai`) is a partner platform. An Agent that joins
Arena by following Arena's own `skill.md` burns minutes of LLM tool calls — yet
the actual registration is a single sub-second HTTP POST. `ArenaOnboarder`
moves registration **and** skill-file installation to the server so a
provisioned Agent lands already registered and ready to compete: the user just
starts chatting.

The class is deliberately dependency-light: give it a **workspace path** and it
does the whole thing (register → fetch SKILL.md → write the `arena` skill +
credentials). No DB handle, no `settings`, no SkillModule instance required — so
any script or the future `ArenaProvisioningService` can call it the same way.

## Upstream / Downstream

**Calls out to:** Arena's public API — `POST /api/v1/agents/register`,
`GET /api/v1/agents/me`, `GET arena42.ai/skill.md`, and the platform-only
`POST /api/v1/agents/me/platform-bind-owner` (owner-email binding). Pure
`httpx`; the client is injectable for tests.

**Writes into:** an agent workspace at `<workspace>/skills/<skill_name>/`
(`SKILL.md`, `.skill_meta.json`, `credentials.json`). The `.skill_meta.json`
layout **must stay byte-compatible with `module/skill_module/skill_module.py`** —
its `get_all_skill_env_vars()` base64-decodes `env_config` at runtime, so the
running agent's SkillModule reads `ARENA_API_KEY` / `ARENA_AGENT_ID` back with no
extra wiring. If SkillModule's meta format changes, this writer changes with it.

**Consumed by:** the Arena onboarding feature (`feat/arena-onboarding`). The
spike `scripts/spike_arena_provision.py` wraps the DB side (agent + instances +
awareness) around it; `scripts/verify_arena_onboarding.py` is the usability
check.

## Design decisions

**Direct API, not `@netmind/arena-cli`.** Verified 2026-06-15: the CLI stores to
a machine-global `~/.config/arena/credentials.json` (one-agent-per-host
assumption) — wrong for our multi-tenant cloud where many agents share one
backend host. The direct API lets each agent's credentials live isolated in its
own workspace. The CLI is also no faster (both are one HTTP round-trip; the CLI
adds node startup).

**Nintendo-style three-group gamertag names.** Arena names must match
`[A-Za-z0-9_]` (else HTTP 400 VALIDATION_ERROR) and be globally unique (dup →
HTTP 409 NAME_TAKEN). Three 24-word groups give 13,824 base combos; on 409 the
generator re-rolls, then appends a `_NN` suffix. `register(name=None)` uses the
register call itself as the uniqueness oracle — the 201 that proves a name is
free is the same call that claims it, so there is no check-then-act race.

**No DB / settings coupling.** Keeps the class reusable from any context and
trivially unit-testable (inject `http_client` + a seeded `rng`).

**`bind_owner` is best-effort and never raises (2026-06-23).** Arena normally
binds an owner email via a user-clicked verification link; because we provision
programmatically there's no inbox to click, so Arena exposes
`platform-bind-owner` — authenticate as the agent (its api_key) and pass the
user's NetMind JWT in the body, and Arena verifies the JWT against the shared
NetMind account system and writes `agent.ownerEmail`. Owner email is *optional*,
so this method maps every outcome to a status string instead of raising:
`bound` / `skipped_no_email` (200, account has no email) / `already_bound` (400
EMAIL_ALREADY_BOUND — idempotent success) / `invalid_token` (401) /
`rate_limited` (429) / `error` (anything else, missing api_key/token, transport).
`already_bound` is treated as a terminal success so callers can skip retries.
The NetMind JWT is forwarded, never stored.

## Gotchas

- **`register(name=...)` with an explicit name raises on 409** rather than
  re-rolling — only the `name=None` path retries. Callers wanting a specific
  name must handle the collision.
- **`referralCode` is optional** — registration needs no partner secret and
  grants 200 credits. A `referral_code` ctor arg is plumbed through for when we
  want attribution.
- **Secret at rest is base64, not encryption** — matches the `lark_credentials`
  precedent. There is an open TODO to move both to `cryptography.fernet` in one
  sweep; don't fork the encryption story here.
- **`onboard(workspace_path)` treats the arg as the agent workspace root** and
  appends `skills/<skill_name>`. Pass the per-agent dir
  (`{base_working_path}/{agent_id}_{user_id}`), not the skills dir itself; use
  the lower-level `install_skill(skills_dir, ...)` if you already hold that.
