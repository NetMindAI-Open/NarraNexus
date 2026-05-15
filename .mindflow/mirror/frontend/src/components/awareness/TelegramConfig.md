---
code_file: frontend/src/components/awareness/TelegramConfig.tsx
stub: false
last_verified: 2026-05-09
---

## Why it exists

Per-agent Telegram bot binding UI inside the Awareness panel's IM
Channels section. Third channel after Lark and Slack; same Card
shape, same fetch/bind/test/unbind action set, same disclosure block
pattern. Consumed by ``IMChannelsSection`` which lists all channels.

The deliberate contrast vs. ``SlackConfig.tsx``: there is NO Slack-
style App Manifest YAML to copy. Telegram setup happens entirely
inside Telegram itself via @BotFather. The disclosure walks the user
through that command sequence with one critical step (``/setprivacy
Disable``) emphasised because skipping it produces silent group-
delivery failures.

## Design decisions

- **Two states only — not bound / bound.** Mirror of the Slack
  component minus the manifest paste-flow. No "intermediate bind in
  progress", no "OAuth callback pending" — Telegram bind is atomic
  from the user's perspective.
- **Disclosure carries the @BotFather command sequence with
  ``<code>`` tags around every command.** Users can click-copy each
  command by selecting the code segment. The "~3 min" badge in the
  toggle bar sets the user's expectation honestly.
- **``/setprivacy → Disable`` is highlighted with ``<strong>``
  ("Critical for groups") plus a yellow warning at the bottom of
  the disclosure ("If group messages don't reach the bot, the FIRST
  thing to check is /setprivacy").** Phase 3 lesson #6 in plan form:
  the most common silent failure deserves the most visible warning.
- **Bot-token field is ``type="password"``** so screen-recordings
  and shoulder-surfing don't bleed the secret. ``autoComplete="off"``
  prevents browser fill from saving it.
- **Client-side regex ``^\d+:[A-Za-z0-9_-]+$`` on the token.**
  Catches obvious typos before the network call. Backend re-
  validates via ``getMe``; the regex is UX, not security.
- **Owner @username field is OPTIONAL with explanatory placeholder.**
  Dropping the trust signal silently is worse than the user
  understanding the trade-off; placeholder text says "enables owner
  trust signal" so the consequence of leaving it blank is visible.
- **State 2 surfaces a warning when ``owner_user_id`` is empty.**
  Yellow "⚠ No owner registered — re-bind with your @username to
  enable" so post-bind the user can see whether their @username
  resolved successfully (it can fail silently — see
  ``_telegram_credential_manager``).
- **``useConfirm`` modal on unbind.** Destructive — explicit
  confirmation pattern shared across the IM channel components.
- **``mountedRef`` guards every async setState.** Prevents the
  classic React warning when the user navigates away mid-fetch.
- **``credential.bot_user_id``** displayed alongside ``@username``
  so the user has the immutable id at hand for support / debugging.

## Upstream / downstream

- **Composed by**: ``IMChannelsSection.tsx`` (registered in the
  ``IM_CHANNELS`` array).
- **Calls**: ``api.getTelegramCredential / bindTelegramBot /
  testTelegramConnection / unbindTelegramBot``.
- **Reads**: ``useConfigStore().agentId``.
- **Types**: ``TelegramCredentialData`` from ``@/types``.

## Gotchas

- The disclosure warning about ``/setprivacy`` is duplicated in the
  agent's ``_NO_BOT_INSTRUCTION`` system prompt. Drift will confuse
  the user vs. the agent — keep wording aligned.
- The token regex matches only the public format. Telegram has
  rumoured local-bot-API tokens with a different shape; if support
  is ever needed, widen here AND in
  ``_telegram_credential_manager.bind``.
- ``actionLoading`` is shared across bind/test/unbind. If two
  actions race (user spam-clicks), the loading spinner is correct
  but the result-set in state may show the latest only. Acceptable
  for v1.
