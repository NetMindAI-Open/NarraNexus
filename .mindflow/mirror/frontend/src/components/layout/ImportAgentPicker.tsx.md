---
code_file: frontend/src/components/layout/ImportAgentPicker.tsx
last_verified: 2026-08-27
stub: false
---

# layout/ImportAgentPicker.tsx — the import list, without any chrome

## Why it exists

Split out of [[ImportAgentModal]] (2026-08-27) so the sidebar modal and step 2
of the first-run flow ([[StepImport]]) render the SAME picker and can only differ
where they should: the buttons around it. State comes from [[useAgentImport]];
this file is presentation only.

## Design decisions

- **Row anatomy**: checkbox · brand mark · title/meta · confidence · chevron.
  The chevron lazily scans that one source and expands its detail inline
  (rename, per-session checkboxes, skills/memory/MCP tiles, plaintext-credential
  warning) — not expanding a row is a valid choice that imports it as scanned.
- **Group headers only when they disambiguate** (a framework with >1 row). A
  one-row tool carries its own icon and label on the row, or the header and the
  row would say the same word twice — and two checkboxes would share one
  accessible name.
- **Multi-row groups start CLOSED** (Owner 2026-08-27). 27 Claude Code projects
  flat on the page read as a wall rather than a choice. The header is the
  primary element — brand mark, tool name, `N of M checked` — and the rows are
  secondary: indented, revealed by clicking the header strip (the whole strip,
  not a 14px chevron). The pre-selection stays legible while closed because the
  header counts it, and the footer counts the whole list either way. A one-row
  group has no header and is therefore always visible; a manual folder scan
  opens its own group, or the row it just added would look like nothing happened.
- **Clicking a row OPENS it; only its checkbox selects it** (Owner 2026-08-27).
  Selecting was the wrong default action for a strip whose whole point is "let me
  look inside first", and selection already has a dedicated 18px target. The
  chevron is folded into that same button rather than being a second one — two
  buttons per row meant two accessible names competing for the same row.
- **Select-all sits at the same level as the tool rows** — same padding, same
  13px type. It is the same kind of control (a checkbox owning a set of rows), it
  just owns all of them; styling it as a mono micro-label made it read as a
  section header instead.
- **No dividing rules between rows** (Owner 2026-08-27: "去掉横线,做成 hover
  阴影"). Separation is a 2px gap plus `--nm-elev-1` on hover, so the list reads
  as a stack of cards instead of a striped table. The selected fill
  (`--nm-row-active`) stays — with no border to lean on, it is the only signal
  that a row is checked. Written up as a pattern in design_system §2.5.
- The running/done phase is the SAME list re-skinned, not a new screen: the user
  watches the rows they just checked turn into results.
- `FrameworkIcon` resolves the mark via `createElement` on purpose —
  `const Icon = frameworkIcon(fw)` in a component body is a new component
  identity every render (`react-hooks/static-components`).
