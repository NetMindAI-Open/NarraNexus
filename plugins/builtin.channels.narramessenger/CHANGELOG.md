# Changelog

## Unreleased

- The narra_cli failure instructions on all three agent-facing surfaces
  (`_CLI_CAPABILITY`, `resources/narra-runtime.md`, `_narra_guide._BUILTIN`) now
  pass `dedup_key="narra_cli:<code>"` to `submit_feedback` — that key is what
  makes the report one per agent per code, and a caller omitting it gets no
  deduplication at all. They also relay whether the team was notified from the
  call's result instead of asserting it.

## 1.0.0

- Packaged as an independent workspace member (plugin platform batch 6b).
