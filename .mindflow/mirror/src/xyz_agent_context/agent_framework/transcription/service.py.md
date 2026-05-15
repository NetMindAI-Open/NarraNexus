---
code_file: src/xyz_agent_context/agent_framework/transcription/service.py
last_verified: 2026-05-07
stub: false
---

# service.py — TranscriptionService facade

## Why it exists

Single import surface for the upload route and the availability
endpoint. Keeps the resolver/backends/url_signer split from leaking
into route code, and gives tests one seam (`override_backends`) to
swap implementations without monkeypatching internals.

## Behaviour

- `is_available(user_id)` — cheap pre-check, no backend call. Used by
  `/api/transcription/availability` and the legacy `transcription_available`
  echo on upload responses.
- `availability_reason(user_id)` — returns `(bool, str)` where the
  string is a frontend-readable reason code (`has_openai`,
  `has_netmind`, `has_other`, `system_free_tier`, `none`). Lets the
  click-time dialog vary its copy.
- `transcribe(*, file_path, file_id, agent_id, user_id, language=None)`
  — walks resolver candidates with a per-backend overall timeout;
  returns the first non-empty transcript or `None`. Never raises.

## Why "first match wins" and not concurrent fan-out

Cost. Even free-tier NetMind takes ~20s; firing N backends in parallel
on every upload would multiply that linearly across the user's
configured providers. The serial walk is also the "user said:
prefer X" semantics most users expect. The trade-off: one slow
candidate adds its full timeout (35s for OpenAI, 60s for NetMind) on
top of any successful one. We absorb that — the per-backend timeout
matrix is the safety valve.

## Gotchas

- `instance()` caches a singleton. Tests that swap settings should call
  `reset_instance()` first; otherwise the previously cached service
  still references the old resolver state.
- Backends are looked up by `cred.backend_kind.value` (the string).
  If you add a new `TranscriptionBackendKind` member, register the
  implementation in `__init__` AND in the
  `backends/__init__.py::__all__` re-export.
- A backend that violates the never-raise contract is logged at
  `logger.exception` and the next candidate is tried. Don't catch
  this in the upload route — it's the service's problem to wallpaper
  over backend bugs gracefully.
