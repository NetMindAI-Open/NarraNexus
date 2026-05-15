---
code_file: src/xyz_agent_context/agent_framework/transcription/credential.py
last_verified: 2026-05-07
stub: false
---

# credential.py — resolved transcription provider record

## Why it exists

The resolver's output is a list of these. Each one carries the
`backend_kind` tag the service uses to dispatch, plus the `api_key /
base_url / model` the chosen backend needs. Decoupling
"which provider to use" from "how to call it" lets us add backends
(OpenRouter base64, on-device whisper.cpp) without changing the
resolver surface.

## Why backend_kind is a separate enum, not a string field on ProviderConfig

The user-visible `ProviderConfig` (chat / embedding / helper_llm) has
exactly one `protocol` field (openai / anthropic). Tying transcription
backend identity to that field would force the user to think about
"which transcription backend" — the explicit product call (spec §3) is
that they should NOT have to. Resolver derives the backend from
`base_url` and stamps it on this dataclass internally.

## is_system_free_tier

True only for the cloud-default NetMind credential built from
`settings.system_default_netmind_*`. It's a documentation flag — the
service layer doesn't currently gate on it, and the cost_tracker is
not consulted for transcription anywhere. Kept as an explicit field
so a future "actually meter free-tier audio minutes" change has an
obvious hook.
