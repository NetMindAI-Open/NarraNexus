---
code_file: src/xyz_agent_context/schema/provider_schema.py
last_verified: 2026-04-10
stub: false
---

# provider_schema.py

## Why it exists

NexusAgent must not be locked to any single LLM provider (CLAUDE.md rule #9). This file defines the multi-provider configuration system that allows users to plug in different APIs for different functional roles. A user might use Claude for the main agent loop, a BAAI embedding model for vectors, and a cheap OpenAI-compatible model for auxiliary LLM calls — all configured without code changes.

The entire configuration is serialized to `~/.nexusagent/llm_config.json` by `LLMConfig`, making it portable across runs.

## Upstream / Downstream

`ProviderRegistry` (in `agent_framework/`) reads `LLMConfig` at startup and validates that each slot's assigned provider has a compatible protocol. The `SLOT_REQUIRED_PROTOCOLS` dict in this file is the ground truth for those compatibility checks. The frontend provider configuration panel reads and writes through API routes that ultimately read/write `LLMConfig`. `SlotName` enums drive which configuration widget appears for each slot.

## Design decisions

**`ProviderConfig.linked_group`**: one physical API key (e.g., a NetMind key) can support both Anthropic and OpenAI protocols. The system creates two `ProviderConfig` entries — one for each protocol — and links them via a shared `linked_group` string. This way the UI can show them as a single "card" while the runtime treats them as two separate providers.

**`AuthType.OAUTH`**: this is the Claude Code Login path where the user authenticates via browser OAuth. No API key is stored. The `api_key` field is empty. This was added as a first-class auth type so the system does not need to special-case it in multiple places.

**`SLOT_REQUIRED_PROTOCOLS` as a module-level dict rather than a method on `SlotName`**: this makes it easy to extend the list of protocols a slot accepts without touching the enum definition. Currently `AGENT` only accepts Anthropic (because the agent loop uses the Anthropic SDK's managed agent feature), but `EMBEDDING` and `HELPER_LLM` accept OpenAI-compatible endpoints.

## Gotchas

**`ProviderSource` is "informational, not logic-driving"** (per the docstring). Do not write `if provider.source == ProviderSource.NETMIND: do_something_special()`. The source field is metadata for UI display only. The actual behavior differences are encoded in `protocol` and `auth_type`.

**`LLMConfig.slots` keys are strings** (the slot name values like `"agent"`, `"embedding"`) not `SlotName` enum members. When you load the config from JSON and look up a slot, use `config.slots.get("agent")` not `config.slots.get(SlotName.AGENT)` — unless you know that `SlotName.AGENT == "agent"` (it is, because `str, Enum`).

## New-joiner traps

- The `AGENT` slot requires `ProviderProtocol.ANTHROPIC` because the agent loop uses Claude SDK features that are not available in the OpenAI protocol. If you want to add OpenAI-protocol support for the agent loop in the future, you must update `SLOT_REQUIRED_PROTOCOLS` and implement the adapter in `agent_framework/`.
- `ProviderConfig.models` is a list of model IDs available on that provider. It is populated when the user saves a provider configuration, not dynamically fetched. If a user's subscription changes and new models become available, they need to re-save their provider config.
