---
code_file: src/xyz_agent_context/module/lark_module/_lark_scope_validator.py
last_verified: 2026-05-27
stub: false
---

# _lark_scope_validator.py — required-vs-optional scope completeness check

## Why it exists

`do_bind` used to verify only that the App ID + Secret could mint a
tenant_access_token. That's necessary but not sufficient — the bot
also needs specific permission scopes (`im:message` to receive,
`im:message:send_as_bot` to reply, `contact:user.base:readonly` to
resolve sender names). Without them, bind succeeds, then the bot
silently never responds or all sender names show as "Unknown". This
module compares the app's actual granted scopes (via
`lark-cli auth scopes --format json`) against curated REQUIRED +
OPTIONAL lists and returns a structured diff.

## Upstream / Downstream

- **Called by**: `_lark_service.do_bind` after the credential verify
  step succeeds; also exposed via `get_scope_policy()` for the
  diagnostic MCP tool layer (Stage 3 / Layer 2).
- **Calls**: `LarkCLIClient._run_with_agent_id(["auth", "scopes", ...])`
  — passed in (not imported) so tests can mock without monkey-patching.

## Policy

**REQUIRED_BOT_SCOPES** — bind blocks if missing:
  - `im:message`, `im:message:send_as_bot`, `im:resource`,
    `im:chat`, `im:chat:readonly`

**REQUIRED_USER_SCOPES** — bind blocks if missing (strict UX call —
strictly the bot still receives/replies, but "Unknown" sender names
hallucinate identities, so we treat as required):
  - `contact:user.base:readonly`

**OPTIONAL_SCOPES** — bind succeeds with warning:
  - docs / drive / calendar / sheets / wiki / task / contact.email

## Design decisions

**Fail-OPEN on tooling errors.** If `lark-cli auth scopes` fails (CLI
hiccup, JSON parse error, timeout), we return `check_ran=False` and
`do_bind` does NOT block. The principle: don't punish the user for
our tooling problem. A blocked bind because our scope check broke
would be worse UX than a bind that "succeeded" with potentially-
missing scopes.

**Defensive JSON parsing.** The exact shape of `auth scopes` output
isn't pinned in lark-cli source we can see. We accept several common
shapes: `{bot_scopes: [], user_scopes: []}`, `{botScopes, userScopes}`,
and flat `{scopes: [{scope, token_types}]}`. If a future lark-cli
changes the shape further, `_extract_scope_list()` is the one place
to update.

## Gotchas

- `REQUIRED_USER_SCOPES` blocks bind. If we ever want to soften this
  to "warn instead", move `contact:user.base:readonly` from REQUIRED
  to OPTIONAL — no other code change needed.
- The OPTIONAL list grows as integrations expand. Adding a new entry
  is one line; users won't see a regression because optional scopes
  only ever produce warnings, never blocks.
