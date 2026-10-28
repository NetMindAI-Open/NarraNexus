/**
 * @file_name: backendTs.ts
 * @date: 2026-08-24
 * @description: The ONE parsing rule for backend timestamp strings.
 *
 * Backend datetimes are UTC, but two serialization shapes reach the wire
 * (review #349 I1): SQLite round-trips an aware `...+00:00` string, while
 * MySQL's DATETIME(6) strips tzinfo and the naive value serializes with NO
 * offset — which a browser's bare `Date.parse` reads as LOCAL time,
 * skewing every consumer by the viewer's UTC offset (invisible on a local
 * SQLite dev setup, wrong in cloud).
 *
 * `_format_dt` on the backend now attaches UTC to naive values, but this
 * parser stays tolerant of both shapes so a backend regression cannot
 * flip timestamps again: a string with an explicit offset (Z or ±hh[:]mm)
 * is trusted as-is — never blind-append 'Z', that turns `...+00:00` into
 * an Invalid Date — and an offset-less string is interpreted as UTC.
 *
 * Every run_reconnect / observation-frame timestamp consumer must go
 * through here rather than calling Date.parse directly; two consumers
 * each remembering the rule is how the wsManager/useRunObservation drift
 * happened.
 */

const HAS_OFFSET = /(?:Z|[+-]\d{2}:?\d{2})$/;

/** Epoch ms for a backend timestamp string; NaN when absent/unparseable. */
export function parseBackendTs(raw: string | null | undefined): number {
  if (!raw) return NaN;
  return Date.parse(HAS_OFFSET.test(raw) ? raw : `${raw}Z`);
}
