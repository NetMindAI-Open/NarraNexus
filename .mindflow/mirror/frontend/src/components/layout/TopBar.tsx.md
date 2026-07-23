---
code_file: frontend/src/components/layout/TopBar.tsx
last_verified: 2026-07-22
stub: false
---

# layout/TopBar.tsx — narrow global status strip + ⌘K trigger

## Why it exists

A single 36px strip above the whole app that holds only *glanceable, global*
state plus one global action, so it never competes with the sidebar, bookmark
strip, or chat header for attention. It answers "where am I / what runtime am I
on" at a glance and offers the one cross-app action (⌘K jump). It is a separate
file because its content is intentionally scoped: things that are per-agent
(token cost chip) or that would duplicate the sidebar (user menu / theme /
logout) are deliberately kept *out* of here.

## How it works / design

- Left side: mobile hamburger + `BindingDot` + a breadcrumb derived from the
  route (`pageLabel`) falling back to the selected agent's name. Right side: a
  "Find Us" community pill, a LOCAL/CLOUD runtime label with an online dot,
  and the ⌘K palette trigger.
- The "Find Us" pill (ops requirement, 2026-07) is a plain external link to
  the marketing site's social hub (`FIND_US_URL` →
  https://www.narra.nexus/connect) — it exists to shorten the "sign up → join
  the community" path, sits deliberately LEFT of the runtime label
  (`topBarFindUs.test.tsx` pins the order), and must stay an `<a
  target="_blank" rel="noopener noreferrer">`, not in-app routing. Its label
  reuses the marketing site's `nav.findUs` translations
  (`layout.topBar.findUs`) so both surfaces speak with one voice. It is the
  only filled pill in the strip — that is its prominence mechanism inside the
  monochrome NM palette; don't add more filled pills here or it stops
  standing out.
- Upstream: rendered once by [[MainLayout]] at the top of the app shell.
  Downstream: reads agents/`agentId` from [[useConfigStore]], `mode` from
  [[runtimeStore]], `toggleMobileNav` from [[uiStore]]; owns and renders
  [[CommandPalette]].
- Owns the global ⌘K / Ctrl+K keydown listener (window-level) that toggles the
  palette — the palette itself is presentational and stateless about its own
  open flag.
- Gotcha: the hamburger is `md:hidden` and drives [[uiStore]]'s mobile nav
  drawer; on desktop the sidebar is in normal flow so the toggle is inert. A
  cross-agent unread bell is a planned addition once a rollup exists — left out
  on purpose rather than faked.
