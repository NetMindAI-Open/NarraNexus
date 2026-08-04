---
code_file: src/xyz_agent_context/agent_framework/anthropic_usage.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 显式导出 uncached 桶

返回值新增 `uncached_input_tokens`（= 原始 `input_tokens` 字段，即全价那一桶）。
`input_tokens` 仍是三桶之和，语义不变。

加它是因为开始有调用方要**分桶计价**（[[anthropic_helper]] / [[cli_helper]]），
否则每个调用方都得自己写 `input - cw - cr`。那个减法一旦符号写反就是一次静默的
10 倍误差（cache read 计价 0.1x），而且错得毫无征兆 —— 与其让三处各写一遍，
不如在产出侧算一次。


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
