---
code_file: frontend/src/components/settings/OneKeyOnboard.tsx
last_verified: 2026-06-10
stub: false
---
## 2026-06-10 (4th pass) — success panel + key verification feedback

The card now shows an explicit success panel (role=status) built from
the onboard response: "You're all set — Agent: <model> (<framework>) ·
Helper: <model>". Needed because in Settings a silent success read as
"nothing happened" (on /setup the navigation masked it). When the
backend's live key probe was inconclusive (key_check="unverified
(...)"), an amber warning line tells the user to re-check the key if
the first chat fails. A definitively bad key never reaches this panel —
the backend rejects with 400 before writing anything.

## 2026-06-10 (3rd pass) — Anthropic default; CLI sign-in pointer in the intro

Provider list reordered/relabelled: "Anthropic (official)" is FIRST and
the default selection (was NetMind-first with a "(Recommended)" tag);
"Claude (Anthropic official)" wording dropped. Get Key link is now
"Get your {keyName} API key". The intro mentions CLI sign-in
(Claude Code / Codex) lives in Advanced — with the honest caveat that
the helper model still needs an API key (OAuth credentials cannot make
direct Messages/Chat-Completions calls, so CLI login alone cannot
complete a runnable setup).

## 2026-06-10 (later) — provider picker; now THE shared quick-setup module

Reworked from auto-detect-only into the single quick-setup surface for
both /setup AND Settings → Providers Step 1 (it replaced
ProviderSettings' old Quick Add block). Provider dropdown covers the
five one-key sources — NetMind (recommended, default), official Claude,
official OpenAI, Yunwu, OpenRouter — each with its Get Key link.
Submission always goes through POST /api/providers/onboard, which also
switches the agent framework (an official OpenAI key needs codex_cli —
the old add_provider+default_slots path couldn't do that) and
(re)assigns BOTH slots: "make this key my active setup" semantics.
Prefix detection survives as a mismatch nudge ("Looks like a Claude
key — switch?") rather than the primary mechanism, since aggregator
keys have no recognisable prefix.

# OneKeyOnboard.tsx — single-key first-run card

## Why it exists

The first-run /setup page used to drop the entire 1400-line
ProviderSettings on a brand-new user (4 provider paths × protocol
concepts × a 2-slot model matrix). This card collapses onboarding to ONE
input: paste a Claude or OpenAI key → `POST /api/providers/onboard`
wires the agent framework + provider + both slots server-side →
onComplete navigates to chat.

## Upstream / downstream

- **Rendered by**: `pages/SetupPage.tsx` as the primary surface
  (ProviderSettings lives behind the page's "Advanced setup"
  disclosure).
- **Calls**: `api.onboard(key, providerType?)` — providerType is only
  sent when the user manually overrode detection; otherwise the backend
  decides from the sk-ant- prefix.
- **UI primitives**: nm PaperCard / FormField / TextInput + ui Button.

## Design decisions

- Detection is purely cosmetic on the frontend (`sk-ant-` → Claude);
  the backend re-derives it. The "use X instead" link sets an explicit
  override which IS sent — typing again clears the override so
  detection re-engages.
- Errors surface from the thrown Error's message (api.ts request()
  extracts FastAPI's `detail` into it); FormField renders it with
  role="alert".
- No API base or headers handled here — everything rides api.ts
  (X-User-Id / JWT attached centrally).

## Tests

frontend/src/__tests__/one-key-onboard.test.tsx — detection, override
call shape, success → onComplete, error surfacing, disabled state.
