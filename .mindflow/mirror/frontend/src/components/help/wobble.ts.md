---
code_file: frontend/src/components/help/wobble.ts
last_verified: 2026-06-11
stub: false
---

# wobble.ts — Hand-drawn SVG stroke generators

## 为什么存在

The overlay needs rough.js-style strokes (arrow / line / open ellipse)
without taking a dependency for ~80 lines of math (spec §12.4 chose
self-written, zero-dep).

## 设计决策

Jitter is **deterministic** — seeded from the input coordinates via a
sin-hash — so identical geometry always yields identical paths and
re-renders never make the strokes "swim". `Math.random()` would also
break any future snapshot testing.

## 2026-06-11 (round 4)

Ellipse rebuilt as four cubic-Bezier arcs (kappa 0.5523) with ±4%
per-quadrant radius wobble — a smooth pen ellipse; the old 8-segment
line loop read as a polygon.
