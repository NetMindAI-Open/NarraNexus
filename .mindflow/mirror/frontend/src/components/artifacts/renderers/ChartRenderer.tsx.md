---
code_file: frontend/src/components/artifacts/renderers/ChartRenderer.tsx
last_verified: 2026-05-27
stub: false
---

## 2026-05-27 — break the Dismiss-modal loop (P0 fix)

Same fix as HtmlRenderer: load effect deps go from `[url, artifact.artifact_id,
registerChartInstance, heal]` to `[url, artifact.artifact_id,
registerChartInstance]`, with `heal.attempt` accessed via `attemptRef`.
Pre-fix the `heal` object's identity churn re-fired the chart fetch on every
hook state transition and bounced the modal back open after Dismiss. See
`useArtifactHeal.ts.md` for the cross-renderer pattern.

## 2026-05-14 — drop `version` prop, fetch via `useArtifactRawUrl`

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The renderer no longer takes a `version` prop. It calls `useArtifactRawUrl`
to mint a view token and fetches the chart JSON from the token-protected
public directory URL (no Authorization header required — the token IS the auth).

# ChartRenderer.tsx — ECharts renderer for application/vnd.echarts+json artifacts

## Why it exists

Renders ECharts option JSON emitted by an agent as an interactive chart. ECharts (~700 KB min+gzip) is intentionally loaded via dynamic `import('echarts')` rather than a static import, keeping it out of the initial bundle and out of the `vendor-echarts` manualChunk unless this component actually mounts.

## Upstream / Downstream

- **Used by**: `ArtifactColumn` via `React.lazy`, dispatched when `artifact.kind === 'application/vnd.echarts+json'`.
- **Calls**: `rawUrl()` from `@/types/artifact` for the fetch target, then `echarts.init()` + `c.setOption()` on the resolved `<div>` ref.

## Design decisions

**Dynamic `import('echarts')` inside `useEffect`.** Two-level laziness: React.lazy defers the component itself; `import('echarts')` defers the library until the component actually runs its effect. This means the ~700 KB library is not downloaded even if `React.lazy` pre-fetches the component module.

**`disposed` flag + `chart.dispose()`.** The async IIFE inside `useEffect` has multiple `await` points. If the component unmounts between them, `ref.current` may be null and calling `echarts.init(null)` would throw. The `disposed` flag short-circuits after each `await`. `chart.dispose()` in the cleanup function releases the canvas WebGL context so it can be GC'd and not leak across tab switches.

**No resize observer.** The chart is initialised once at the div's current dimensions. If the panel is resized, the chart will not adapt. A `ResizeObserver` + `chart.resize()` call can be added without changing the contract — defer until a user reports it.

**Agent is responsible for valid option JSON.** No schema validation is performed on the fetched payload. A malformed option silently produces an empty or broken chart; the error boundary (if any) in `ArtifactColumn` will catch thrown exceptions from `c.setOption()`.

## Gotchas

`echarts.init(ref.current)` will fail if `ref.current` has zero dimensions (display:none, width:0). The tab system must ensure the container has non-zero dimensions before the renderer mounts.

**Stale error reset (I5, 2026-05-09)**: `setError(null)` is now called at the top of the effect, before the async IIFE. Without this, a failed fetch on version N would leave `error` set, and when version N+1 arrives the early-return `if (error) return ...` path would keep showing the stale error instead of attempting a fresh load. The synchronous `setError(null)` inside the effect (before the async IIFE) does not trigger `react-hooks/set-state-in-effect` because the rule only fires when setState is called both synchronously and asynchronously in the same `.then()/.catch()` chain — the async IIFE wrapping here uses `await`/`catch` instead.
