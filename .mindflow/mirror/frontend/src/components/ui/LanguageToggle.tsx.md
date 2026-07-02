---
code_file: frontend/src/components/ui/LanguageToggle.tsx
last_verified: 2026-06-25
stub: false
---

# LanguageToggle.tsx — sidebar-footer language switcher

A `Popover` (opens upward) over [[i18n-index|SUPPORTED_LANGUAGES]], rendered
in the `Sidebar` footer next to [[ThemeToggle]]. Trigger is a `Globe` icon +
the current language `code`; the menu lists every language by flag + label
and calls `i18n.changeLanguage`. Driven entirely by `SUPPORTED_LANGUAGES`, so
adding a language needs no change here.

Styling pairs it with [[ThemeToggle]] so the two read as one control cluster:
muted `--text-secondary` by default, brand-carbon (`--color-carbon`) text on
hover with **no** background highlight. Note the hover-bg override uses
Tailwind **v4** important syntax (`hover:bg-transparent!`, suffix `!`) to beat
the ghost `Button` variant's own `hover:bg-*`; the v3 prefix form
(`!bg-transparent`) generates nothing under v4.
