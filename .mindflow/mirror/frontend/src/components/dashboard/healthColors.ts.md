---
code_file: frontend/src/components/dashboard/healthColors.ts
last_verified: 2026-06-24
stub: false
---

# healthColors.ts — single source of truth for AgentHealth → Tailwind/CSS classes

## Why it exists

Centralises the mapping from an agent's `AgentHealth` to the four visual slots
it drives (rail, card tint, emphasis text, accent), so swapping the palette is
a one-file edit instead of a grep across every dashboard component. It also owns
the `acknowledgedHealthOf` derivation that decides what to render after the user
dismisses attention banners.

## How it works / design

- `HEALTH_COLORS` is a `Record<AgentHealth, HealthColors>` — the exhaustive
  `Record` forces a compile error if [[api.ts]] adds an `AgentHealth` member
  without a colour entry here, so a new state can't silently render `undefined`.
- Consumed by [[AgentCard]] / [[Sparkline]] for the rail/tint/accent; the rail
  uses a vertical gradient + inset-shadow glow (v2.2 G4) but keeps the same
  primary hue per state so existing `bg-emerald`/`bg-red` regex assertions still
  match. [[DashboardSummary.tsx]] currently re-hard-codes its chip colours
  rather than reading from here — known duplication debt.
- The `healthy_idle` mapping is the "silicon" polish: rail + accent use the
  brand `--color-silicon` (the agent-species colour) instead of a Tailwind sky
  blue, while the status TEXT goes neutral `--text-primary` ink — the coloured
  rail alone signals "idle", so the "Idle · last active …" line must read as
  plain text, not a blue link. [[DashboardSummary.tsx]]'s idle chip mirrors this.
- Security invariant S-M1: `acknowledgedHealthOf` must NEVER downgrade `error`
  to a healthy state. When all banners are dismissed, `error` → `acknowledged`
  (neutral slate rail + a small red ack dot drawn by AgentCard, "you saw it,
  it's not fixed"); only warning/paused — lower severity or user-initiated —
  are allowed to fall back to healthy_idle/healthy_running. Don't relax this.
