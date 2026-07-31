---
code_file: src/xyz_agent_context/agent_framework/anthropic_usage.py
last_verified: 2026-07-28
stub: false
---

# anthropic_usage.py — provider-neutral Anthropic usage totals

## Why it exists

Anthropic usage separates uncached input, cache creation, and cache reads.
Cost records and the usage UI expose a provider-neutral input total, so every
Anthropic-backed path must combine those fields identically. Keeping that rule
in one helper prevents the Messages API helper and Claude CLI helper from
quietly reporting different totals.

## Design decisions

- Accept both SDK objects and CLI dictionaries because the two Anthropic
  surfaces expose equivalent fields in different containers.
- Include cache creation and cache reads in `input_tokens`; unlike Anthropic,
  OpenAI already includes cached input in its input total.
- Preserve the cache breakdown in the normalized result for callers that need
  detailed telemetry.
- Missing usage normalizes to zeros so the existing missing-usage warning
  policy remains at each call site.

## Upstream / downstream

- Used by `llm/anthropic_helper.py` for structured attempts and streams.
- Used by `llm/cli_helper.py` for Claude Code one-shot helper calls.
- Its provider-neutral totals are persisted through `cost_tracker.record_cost`.
