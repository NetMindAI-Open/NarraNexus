---
code_file: frontend/src/lib/runtimeConfig.ts
last_verified: 2026-09-11
stub: false
---

# runtimeConfig.ts — runtime deploy-time config, injected via /config.js

## 2026-09-09 — B-40: production bundles fall back to PROD NetMind, never protago-dev

`getNetmindConfig()`'s last-resort fallback now depends on the build kind:
`import.meta.env.DEV` (the `vite` dev server) keeps the protago-dev defaults
(`_DEV_NETMIND`); any `vite build` output (DMG, cloud image) uses
`_PROD_NETMIND` (auth-api / accounts .netmind.ai). The dev literals are dead
code in a production build and get stripped — verified: the pre-fix `vite build`
bundle contained `protago-dev`, the post-fix one does not, and
`scripts/release/check_desktop_netmind_env.sh bundle` asserts that for the DMG.
Precedence is unchanged (injected /config.js → VITE_* → fallback); cloud prod
and dev both inject all four keys (checked their live /config.js), so they are
unaffected.

Why: upstream NetMindAI-Open/NarraNexus#90 ("Sign in with GitHub" on the local
version opened "Netmind AI Test by protagohhz" → accounts.protago-dev.com). The
DMG was not the leak (its build logs show the prod VITE_* reached vite); the
source-run local version was — run.sh → dev-local.sh enabled Power login on
protago-dev and exported no `VITE_NETMIND_*`, so the vite dev server used these
dev fallbacks. That path now exports explicit PROD values
(`scripts/dev/netmind_env.sh`); this change closes the same hole for every
built bundle. Tests: `runtimeConfig.netmind.test.ts` (DEV → protago-dev,
PROD → netmind.ai with no protago-dev string, VITE_* beats both).

Guard for the cloud dev stack (PR#403 review I2): before this change the
fallback happened to equal the dev stack's target, so a dev host `.env` missing
`NETMIND_AUTH_API_URL` / `NETMIND_ACCOUNTS_URL` was harmless; now it would land
on PROD NetMind while the dev backend validates against protago-dev. The bundle
cannot tell which stack it is on, so `getNetmindConfig()` in forced-cloud mode
`console.error`s once per missing injected `authApi` / `accountsUrl`, naming the
`.env` key to set (`_reportUninjectedCloudNetmind`). Desktop/local builds are
silent (they use VITE_*). The fail-fast half belongs to the deploy repo
(compose `${NETMIND_AUTH_API_URL:?...}` / `${NETMIND_ACCOUNTS_URL:?...}`,
tracked as a follow-up there; `NETMIND_REGISTER_URL` stays optional). Tests:
logs once per key when cloud injects empty values, silent with both injected,
silent outside cloud.


## 2026-08-12 — Web-analytics id (GTM), host-gated

Added `WebAnalyticsConfig` + `getWebAnalyticsConfig()` (`gtmId` only). Precedence:
injected `/config.js` `gtmId` key present (even `""` = kill-switch) → `VITE_GTM_ID`
→ compiled-in default **only on an official production host**
(`_OFFICIAL_ANALYTICS_HOSTS` = `agent.narra.nexus`) → `""`.

**The gate is the real hostname, NOT `isForcedCloud()`** — this is the crux. The
deploy stack defaults `NARRANEXUS_FORCE_MODE=cloud`, so mode-based gating would
be true for every self-host and would leak their users' data into our container.
Two intentionally-diverging empty-string semantics live in this file now: `pick`
for analytics treats an injected `""` as "force off" (branches on `key in
injected`), whereas `_str`/netmind treats `""` as "unset, fall through" — the
docstring calls this out so a future field-adder doesn't copy the wrong half.
Sole consumer is `lib/analytics/webAnalytics.ts` (adds `isTauri()` + opt-out
gates). Clarity is deliberately not wired (session replay would capture chat).
The injected-key / VITE steps are **latent** — no deploy injects `gtmId` and no
build sets `VITE_GTM_ID` today, so production's only live source is the host +
constant; the immediate off-switch is the GTM console, not a redeploy.

## 2026-07-13 — Power-login availability + compiled-in dev NetMind defaults

Added `isPowerLoginAvailable()` (frontend twin of the backend
`is_power_login_enabled()`): true when forced-cloud, OR `VITE_ENABLE_POWER_LOGIN`
is truthy, OR /config.js injected NetMind endpoints. `getNetmindConfig()` now
resolves each field with precedence **injected /config.js → VITE_\* → compiled-in
dev default** (protago-dev), so desktop/Tauri and `npm run dev` builds (which
have no /config.js) can still offer Power login. **This changed a prior
behaviour**: with nothing injected, `getNetmindConfig()` no longer returns empty
strings — it returns the dev defaults (the "empty when nothing injected" test was
updated). Availability is deliberately NOT keyed on those dev defaults alone
(they're endpoint VALUES, not the on/off decision), so it stays in lockstep with
the backend flag and we never show a Power entry the backend would 404.

## 2026-06-11 — NetMind endpoint config keys

Added `NetmindConfig` interface and `getNetmindConfig()` function to expose four
NetMind-specific endpoints that vary between dev and prod environments:
`authApi`, `accountsUrl`, `sysCode`, `registerUrl`. These are injected at deploy
time via `window.__NARRANEXUS_CONFIG__` (same mechanism as `mode`/`apiUrl`) so
one built bundle can serve multiple environments without rebuilding.

## Why this file exists

The frontend is built once and deployed to many environments (local, dev EC2,
prod EC2, future tenants). Build-time env vars (VITE_*) would require a
separate build per environment. Instead, the deploy pipeline writes a `/config.js`
before nginx boots, populating `window.__NARRANEXUS_CONFIG__` with the correct
values for that deployment. This file is the single read point for all such
runtime-injected config.

## This file does NOT do

- It does not store user preferences or session state.
- It does not handle feature flags (those are separate).
- It does not validate that injected values are reachable — it only reads and
  normalises them.

## Upstream / downstream

- **Injected by**: deploy pipeline entrypoint script writes `/config.js` which
  runs before the Vite bundle loads.
- **Read by**: any module that needs to know the deployment mode (`isForcedCloud`,
  `isForcedLocal`) or NetMind endpoint URLs (`getNetmindConfig`). Currently the
  main consumers are auth hooks in `frontend/src/lib/netmindAuth/`.

## Design decisions

- **Runtime injection over build-time VITE_**: one image, many deployments; no
  rebuild required when switching environments.
- **Trailing slash stripping in `_str`**: NetMind API docs show URLs with and
  without trailing slashes; normalising here means callers always get a clean
  base URL they can append paths to with a single `/`.
- **`sysCode` does NOT strip trailing slash**: it is an opaque token, not a URL;
  the `_str` helper is intentionally not used for it.

## Gotcha / edge cases

- **Trigger**: when `window.__NARRANEXUS_CONFIG__` is not set at all (local dev
  without a config.js). **Symptom**: all getters return empty strings / null.
  **Root cause**: the guard `|| {}` returns an empty object, so every key
  resolves to `undefined`, and the type-narrowing falls back to `''`.
- **Trigger**: when a deployer sets `netmindAuthApi` with a trailing slash (e.g.
  `https://auth.example.com/`). **Symptom**: would double-slash any path
  appended by callers. **Root cause**: fixed by `_str` which strips trailing
  slashes from all string-type URL fields.

## Related constraints

- Iron rule #10 — mirror md must be updated in the same commit as any
  behavioural change to this file.
