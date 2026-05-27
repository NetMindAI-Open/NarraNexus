---
code_file: src/xyz_agent_context/module/lark_module/_lark_error_translator.py
last_verified: 2026-05-27
stub: false
---

# _lark_error_translator.py — raw lark-cli error → user-friendly structured

## Why it exists

The Lark/Feishu bind flow used to surface lark-cli's raw stderr / JSON
`error.message` straight to the frontend, where it painted in a red
alert div. Users saw cryptic strings like `99991672 App scope not
enabled` or `Credential verification failed` and had no idea what to
do. This module is what turns those into `{title, message,
action_hint, console_url}` cards the UI renders as a clear
"what happened + what to do + click here" panel. Driven by P0 bug
reports of "bot bind 失败但同事不知道为啥", 2026-05-27.

## Upstream / Downstream

- **Used by**: `_lark_service.do_bind` (and any future caller that
  surfaces lark-cli errors to humans).
- **Calls**: nothing — pure-function module, no I/O. Easy to test in
  isolation, easy to extend (one-line table addition).
- **Mirrors**: frontend `LarkErrorDetail` type in `types/api.ts` —
  field names match 1:1 so backend `to_dict()` deserialises directly.

## Design decisions

**Three-tier lookup**:
1. Numeric code (most reliable) — Feishu OpenAPI error codes like
   `99991672` / `1000040351`. Sourced from `error_data.code` when
   lark-cli parsed the JSON, OR by regexing a leading number out of
   the message.
2. Regex on message text — fallback for errors that have no code
   (CLI not installed, timeouts, validation rejects).
3. Generic — always returns *something* with the raw message
   preserved so the user has some signal even on unknown errors.

**Curated, not exhaustive.** Only errors observed in real binds make
the table; everything else falls to generic. Adding a new entry when
a user reports a confusing error is a one-line dict addition. This
keeps the table from rotting into a mass of speculative mappings.

**No I18n yet.** All strings are English (project rule, 铁律 #1). A
future i18n layer can wrap this output.

## Gotchas

- The numeric-code regex `^\s*(\d{4,12})\b` extracts codes from the
  start of the message. If lark-cli ever starts emitting messages
  like `"connecting to 8000..."`, the `8000` would be misread as a
  code. Currently no observed cases; revisit if false-positives
  appear.
- `severity` is hardcoded to `error` for now. Future use cases (scope
  validator warnings, brand mismatch info) will populate `warning` /
  `info`.
