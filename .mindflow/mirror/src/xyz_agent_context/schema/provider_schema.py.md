---
code_file: src/xyz_agent_context/schema/provider_schema.py
last_verified: 2026-06-10
stub: false
---
## 2026-06-10 — helper_llm slot accepts both protocols

`SLOT_REQUIRED_PROTOCOLS[HELPER_LLM]` widened to `[OPENAI, ANTHROPIC]`. The
resolver dispatches the helper build by the assigned provider's protocol
(openai → Chat-Completions helper, anthropic → Messages-API helper). This is
the schema-level enabler for "one Claude key serves agent AND helper".
Runtime metadata only — no DB change.

## 2026-06-10 — Framework-neutral reasoning params (feat/claude-sdk-adapter-upgrade)

SlotConfig gained two NEUTRAL knobs — `thinking: ""|on|off` and
`reasoning_effort: ""|low|medium|high|max` ("" = auto = adapter passes
nothing). They are deliberately NOT provider dialect (no "adaptive"/
"minimal"): NarraNexus will adapt more frameworks (Codex, pi, ...), so the
slot stores semantics and each agent-framework adapter owns the mapping +
clamping (rule #9). Persisted as `user_slots.params_json` (cloud) and via
the normal LLMConfig JSON dump (local llm_config.json) — both backends
expose them through the same set_slot(..., thinking=, reasoning_effort=)
signature with PUT semantics (omitted = reset to auto). Corrupt or
out-of-vocabulary stored params degrade to auto with a warning instead of
failing config load. Tests: tests/agent_framework/test_slot_reasoning_params.py.


# provider_schema.py

## Why it exists

NexusAgent must not be locked to any single LLM provider (CLAUDE.md rule #9). This file defines the multi-provider configuration system that allows users to plug in different APIs for different functional roles. A user might use Claude for the main agent loop, a BAAI embedding model for vectors, and a cheap OpenAI-compatible model for auxiliary LLM calls — all configured without code changes.

The entire configuration is serialized to `~/.nexusagent/llm_config.json` by `LLMConfig`, making it portable across runs.

## Upstream / Downstream

`ProviderRegistry` (in `agent_framework/`) reads `LLMConfig` at startup and validates that each slot's assigned provider has a compatible protocol. The `SLOT_REQUIRED_PROTOCOLS` dict in this file is the ground truth for those compatibility checks. The frontend provider configuration panel reads and writes through API routes that ultimately read/write `LLMConfig`. `SlotName` enums drive which configuration widget appears for each slot.

## Design decisions

**`ProviderConfig.linked_group`**: one physical API key (e.g., a NetMind key) can support both Anthropic and OpenAI protocols. The system creates two `ProviderConfig` entries — one for each protocol — and links them via a shared `linked_group` string. This way the UI can show them as a single "card" while the runtime treats them as two separate providers.

**`AuthType.OAUTH`**: this is the Claude Code Login path where the user authenticates via browser OAuth. No API key is stored. The `api_key` field is empty. This was added as a first-class auth type so the system does not need to special-case it in multiple places.

**`SLOT_REQUIRED_PROTOCOLS` as a module-level dict rather than a method on `SlotName`**: this makes it easy to extend the list of protocols a slot accepts without touching the enum definition. The static `AGENT` entry is the Claude Code default, while `get_slot_required_protocols()` applies the active coding-agent framework: `claude_code` requires Anthropic protocol and `codex_cli` requires OpenAI protocol. `EMBEDDING` and `HELPER_LLM` accept OpenAI-compatible endpoints.

## Gotchas

**`ProviderSource` is "informational, not logic-driving"** (per the docstring). Do not write `if provider.source == ProviderSource.NETMIND: do_something_special()`. The source field is metadata for UI display only. The actual behavior differences are encoded in `protocol` and `auth_type`.

**`LLMConfig.slots` keys are strings** (the slot name values like `"agent"`, `"embedding"`) not `SlotName` enum members. When you load the config from JSON and look up a slot, use `config.slots.get("agent")` not `config.slots.get(SlotName.AGENT)` — unless you know that `SlotName.AGENT == "agent"` (it is, because `str, Enum`).

## New-joiner traps

- Do not hard-code the agent slot as Anthropic-only in assignment paths. Use `get_slot_required_protocols(slot, agent_framework=...)` so Codex CLI can bind an OpenAI-protocol provider while Claude Code keeps the Anthropic requirement.
- `ProviderConfig.models` is a list of model IDs available on that provider. It is populated when the user saves a provider configuration, not dynamically fetched. If a user's subscription changes and new models become available, they need to re-save their provider config.
