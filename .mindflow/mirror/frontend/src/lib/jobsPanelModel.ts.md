---
code_file: frontend/src/lib/jobsPanelModel.ts
last_verified: 2026-09-10
---

## 2026-09-10 — `paused_spend_cap` 进 attention 与 `STATUS_ORDER`

被日花费上限暂停的 job 需要人看一眼（要么等次日自动恢复，要么手动 Resume），归 attention 段，
排在 `paused_no_quota` 之后。`STATUS_ORDER` 从 11 到 12（测试同步）。

## 2026-09-03 (评审修订) — `afterDeps` 只在一处构造

评审 I1：两个返回点参数名不同（`{ n }` vs `{ count }`），后者在依赖链上已跑过/排队的
job 上把 `{{n}}` 原样渲染出来（`count` 还是 i18next 的复数魔法键）。现在一个
`afterDeps` 段两处复用；测试补「有依赖无排程」与「有依赖有 cron 未 blocked」两条。

# jobsPanelModel.ts — What each band of the Jobs panel should show

Pure view-model extracted during the 2026-08-27 density rebuild. It holds
every "does this band render, and with what" decision for `JobsPanel`.

## Why it exists

The rebuild replaced *always render every band* with *a band renders only when
the data it carries is non-empty*. Those conditions **are** the design, and
conditional rendering is exactly the kind of thing that silently regresses —
a later change adds a status, or flips a threshold, and nobody notices until
a screenshot. Keeping them inline in a 400-line component made them
untestable; here each rule is a function with an assertion behind it
(`lib/__tests__/jobsPanelModel.test.ts`).

## Design decisions

**No i18n inside.** `describeRow` returns translation *keys* plus params and
the caller does the `t()`. That is what lets the row-content rules be asserted
without booting an i18n instance, and it keeps the phrasing decisions in the
locale files where translators can see them.

**`formatTime` is injected, not imported.** `describeRow` takes a formatter and
an optional `now`, so tests pin exact output instead of racing the wall clock.

**`successRate` returns `null`, not `0`, when nothing has finished.** The old
stat strip rendered a flat `0%` on a fresh agent, which reads as "everything
failed" rather than "no data yet".

**Compact interval tokens (`15m`, `2h`) instead of `{{count}} minutes`.** These
strings ship in ten locales including Arabic, whose six plural categories would
have to be authored and kept correct for what is a data readout, not prose. One
key, `jobs.row.every`, with a pre-formatted token.

**Cron humanisation stops at daily + hourly.** `0 9 * * *` becomes `Daily
09:00`; anything with a day-of-week or day-of-month field falls back to the
verbatim expression. A half-understood weekly/monthly rendering is worse than
the exact expression, which anyone scheduling jobs can read.

**`filterOptions([])` is empty, deliberately.** A filter row over zero jobs is
pure chrome — see [[JobsPanel.tsx]] band C.

## Gotchas

- **Timestamp parsing is NOT `parseUTCTimestamp` from `lib/utils`.**
  `next_run_at` / `last_run_at` / `trigger_config.run_at` follow the v2
  timezone protocol: naive ISO already in the user's local wall time, paired
  with an IANA name. `lib/utils` assumes naive means UTC (correct for backend
  `created_at` columns, wrong for these). `parseJobTime` here parses
  browser-local on purpose. A user whose browser timezone differs from the
  job's `*_timezone` sees a relative label offset by that difference; the exact
  stamp plus its IANA name is in [[JobExpandedDetail.tsx]].
- Neither `formatRelativeTime` nor `formatMessageAge` in `lib/utils` could be
  reused: the first is English-only, the second collapses every future
  timestamp to "now" — and a scheduled job's headline fact is a future time.
  Hence the local `formatRelative`.
- `STATUS_ORDER` must stay exhaustive over `JobStatus`. A status missing from
  it gets no chip and no meter segment — it would vanish from the panel rather
  than fail loudly. The test asserts length 11 against the union.

## Upstream / downstream

- **Upstream:** `types/api.ts` (`Job`, `JobStatus`, `TriggerConfig`)
- **Consumed by:** [[JobsPanel.tsx]] (filter chips), [[JobStatusMeter.tsx]]
  (segments + rate + visibility), [[JobRow.tsx]] (second line)
