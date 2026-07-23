---
code_file: src/xyz_agent_context/bootstrap/welcome_templates.py
last_verified: 2026-07-21
stub: false
---

# welcome_templates.py — the bilingual first-run "welcome" artifact

## Why it exists

New agents land with a pinned HTML "welcome" artifact (a live tab). This file is
the **design system** for it: a shared, self-contained page chrome plus the
generic NarraNexus welcome copy. Kept deliberately restrained (serif display,
colored line-icon cards, one tinted hero, no emoji) so it reads as *designed*,
not auto-generated — a point the Owner pushed on hard.

## Upstream / Downstream

**Consumed by:** `bootstrap/profiles.py` (the default profile's
`welcome_artifact`) and `services/arena_provisioning_service.py` (the Arena
profile). Both call `bilingual_html()` + `feature_card()`.
**Rendered into:** a `text/html` pointer-model artifact written to the agent
workspace and registered via `ArtifactService.register` (see
`profiles._create_welcome_artifact`).

## Design decisions

- **`bilingual_html(title, en, zh)`** is the single chrome: a fixed EN/中文
  toggle (EN default), the CSS, and the two language `<div>`s. Scenarios supply
  only the two bodies using its classes (.hero/.kicker/h1/.lead/.grid/.card +
  .c-* color / .icon / .callout / .prompt / .steps / .foot).
- **Placeholder replacement, not str.format** — the inline CSS is full of `{}`,
  so the skeleton uses `__TITLE__/__EN__/__ZH__` `.replace()` to stay safe.
- **`feature_card(color, icon, title, desc)`** builds one icon card; `ICONS`
  holds reusable inline line-SVGs (stroke = currentColor, so the `.c-*` class
  tints them). `_card` is an internal alias used by the default copy.
- **Copy mirrors www.narra.nexus** ("An agent team, ready in one click.", the
  team-first feature set, the open-source / GitHub footer). No "hot-pluggable"
  language (Owner's call).

## Gotchas

- Adding a new card icon means adding an `ICONS` entry; a `.c-<name>` color class
  must exist in the skeleton CSS for the tint.
- The page must stay self-contained (inline CSS/JS, no external fetch) — it is
  served standalone from the artifact raw route inside an iframe.
