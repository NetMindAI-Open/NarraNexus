---
code_file: frontend/src/hooks/useMediaQuery.ts
last_verified: 2026-06-24
stub: false
---

# hooks/useMediaQuery.ts — subscribe to a CSS media query from JS

## Why it exists

Most responsive behavior is done in CSS, but some layouts need JS to *branch
the render itself*, not just toggle styles — e.g. the chat/artifacts split
becomes a tab switcher on phones, which is a different component tree, not a
restyle. This hook gives that JS a reactive boolean for a media query, and the
`useIsMobile` convenience wrapper pins the project's single "mobile ends here"
breakpoint in one place.

## How it works / design

- `useMediaQuery` seeds state from `window.matchMedia(query).matches` and
  subscribes to the `change` event, re-rendering on transitions; SSR-safe
  initial value (`false`) guards the `typeof window` check.
- `useIsMobile` hardcodes `(max-width: 767.98px)`, which mirrors Tailwind's
  `md` breakpoint so JS and CSS agree on where "mobile" ends — change one and
  this must move too.
- Upstream: consumed by responsive layout components (e.g. the chat/artifacts
  area and [[CommandPalette]]'s mobile panel entry path) that need a render
  branch rather than a CSS toggle. Downstream: only the browser
  `matchMedia` API.
- Gotcha: each distinct `query` string is its own subscription; pass a stable
  string (the `useIsMobile` constant) rather than building queries inline to
  avoid resubscribing every render.
