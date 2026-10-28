/**
 * @file tokenFormat.ts
 * @author NarraNexus
 * @date 2026-08-19
 * @description Shared rendering rules for every LLM-usage surface: how a token
 * count is summed, how it is formatted, how USD is formatted, and what a
 * `by_model` key is called on screen.
 *
 * Extracted from CostPopover when a second usage surface (the account page's
 * NarraNexus-usage section) needed the same functions. Two independent copies
 * of "how do we render a token count" drift, and they drift silently — the same
 * week's usage would read 1.2M on one screen and 1.23M on another, and the
 * reader has no way to tell which one is rounded.
 *
 * The summing rules matter more than the formatting ones: they have already
 * caused a real defect (2026-07-30, "input 213" for a 1.2M-token week), and the
 * failure mode is a number off by an order of magnitude rather than a crash.
 *
 * The InnerThoughtCard copy this file used to warn about is gone (2026-08-28):
 * extracting components/chat/RunStatChips for the Conversation view forced the
 * choice, and these rules won. That card's M-scale counts therefore gained a
 * second decimal, and its sub-hundredth-of-a-cent runs now say "<$0.0001"
 * instead of a bare "$0".
 */

/**
 * The three input-side buckets, summed.
 *
 * `input_tokens` is ONLY the full-rate bucket. Cache reads (0.1x) and cache
 * writes (1.25x) are separate columns, and on a cache-warm run they are >99% of
 * what the model actually read. Summing only the first is what produced
 * "input 213" for a 1.2M-token week (2026-07-30, live agent) — the number is
 * wrong by an order of magnitude and nothing looks broken.
 *
 * `?? 0` is load-bearing: a response cached by a frontend running against an
 * older backend has no such keys, and `undefined` in a sum renders "NaN".
 *
 * Deliberately NOT one function that sniffs its argument shape with `in`. The
 * `total_`-prefixed summary shape gets its own pair below; a single clever
 * discriminator stops being clever at the third shape.
 */
export function inputSideTokens(d: {
  input_tokens: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
}): number {
  return d.input_tokens + (d.cache_read_tokens ?? 0) + (d.cache_creation_tokens ?? 0);
}

/** Everything read plus everything written, for a per-model or per-day entry. */
export function totalTokens(d: {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens?: number;
  cache_creation_tokens?: number;
}): number {
  return inputSideTokens(d) + d.output_tokens;
}

/** `inputSideTokens` for the `total_`-prefixed CostSummary shape. */
export function summaryInputSideTokens(s: {
  total_input_tokens: number;
  total_cache_read_tokens?: number;
  total_cache_creation_tokens?: number;
}): number {
  return (
    s.total_input_tokens +
    (s.total_cache_read_tokens ?? 0) +
    (s.total_cache_creation_tokens ?? 0)
  );
}

/** `totalTokens` for the `total_`-prefixed CostSummary shape. */
export function summaryTotalTokens(s: {
  total_input_tokens: number;
  total_output_tokens: number;
  total_cache_read_tokens?: number;
  total_cache_creation_tokens?: number;
}): number {
  return summaryInputSideTokens(s) + s.total_output_tokens;
}

/** Format a token count: 980 → "980", 12345 → "12.3k", 2_400_000 → "2.40M". */
export function formatTokens(n: number): string {
  if (n < 1000) return n.toString();
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`;
  return `${(n / 1_000_000).toFixed(2)}M`;
}

/**
 * Format USD so a real amount never renders as a zero.
 *
 * toFixed(4) still prints "$0.0000" below a hundredth of a cent — and an
 * embedding-heavy day lands there. That is the same failure these panels avoid
 * everywhere else by hiding cost when it is 0: a displayed zero reads as
 * "free", not as "too small to show". Callers already gate on > 0, so anything
 * reaching here is genuinely non-zero and says so.
 */
export function formatCost(n: number): string {
  if (n >= 0.01) return `$${n.toFixed(2)}`;
  if (n >= 0.0001) return `$${n.toFixed(4)}`;
  return '<$0.0001';
}

/**
 * Display label for a `by_model` key from GET /api/agents/{id}/costs.
 *
 * That endpoint does NOT key by model id. It buckets every row into exactly two
 * synthetic keys by call_type (backend/routes/agents/cost.py): `__main_model__`
 * for agent_loop, `__helper_model__` for everything else. Rendering the key
 * verbatim puts a raw `__main_model__` on the user's screen — which is what
 * shipped to the account page before a live check caught it.
 *
 * The date-suffix strip below is therefore unreachable for that endpoint today;
 * it stays as the sane default for any caller handed a real model id.
 */
export function shortModelName(
  model: string,
  labels: { main: string; helper: string },
): string {
  if (model === '__main_model__') return labels.main;
  if (model === '__helper_model__') return labels.helper;
  return model.replace(/-\d{4}-?\d{2}-?\d{2}$/, '').replace(/-\d{8}$/, '');
}
