---
code_file: frontend/src/components/icons/FrameworkBrandIcons.tsx
last_verified: 2026-08-27
stub: false
---

# components/icons/FrameworkBrandIcons.tsx — marks for importable agent tools

## Why it exists

The import list shows one row per agent found on the machine. With every row
carrying the same grey robot, 29 rows read as noise; with each tool's own mark
they read as "your tools". Owner decision 2026-08-27 — design_system §5
(lucide-only) carries the matching exception: third-party **product identity**
may use real marks, UI semantics stay lucide.

Same convention as the sibling [[ChannelBrandIcons]] / [[ModelBrandIcons]]:
vendor assets only, nothing invented.

## Design decisions

- **Claude Code** reuses `ClaudeBrandIcon`; **Codex** reuses `OpenAIBrandIcon`
  but refills it with `--nm-ink` — its canonical `#000000` is invisible on dark
  warm paper (a live bug in ModelBrandIcons for any dark surface).
- **OpenClaw** uses openclaw.ai's own `favicon.svg` (red-gradient lobster),
  vendored at `public/framework-logos/openclaw.svg`.
- **Hermes** (`NousResearch/hermes-agent`) has no square glyph that survives
  16px — their favicon is a 48px engraving that turns to mush — so it is a
  **lettermark** drawn from their wordmark: white serif H on brand blue
  `#0000A5`. Not a fabricated logo, and swappable the moment they ship a glyph.
- **Vendored, not CDN.** The marks are fetched at development time and committed:
  the DMG must work offline (binding rule #7), a remote `<img>` per row would
  leak which tools the user has installed to a third party, and a vendor
  changing their avatar would silently change our UI.

## Gotcha

- Framework → icon matching lives in [[migrationLabels]], not here:
  react-refresh forbids mixing component exports with plain function exports.
