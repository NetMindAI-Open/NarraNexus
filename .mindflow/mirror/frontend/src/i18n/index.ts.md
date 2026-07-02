---
code_file: frontend/src/i18n/index.ts
last_verified: 2026-06-26
stub: false
---

# i18n/index.ts — react-i18next bootstrap + glob locale loader

Initialises the single app-wide `i18next` instance (imported once for its
side effect from `main.tsx`, before render). Scope is **UI chrome only** —
agent replies come from the LLM in the user's own language and are never
routed through `t()`.

Key design decisions:

- **Glob-loaded resources.** Each language is a single `locales/<lang>.json`,
  assembled with `import.meta.glob('./locales/*.json', {eager:true})` + a
  small `deepMerge` (now a no-op per language, kept as a defensive seam).
  English is the source of truth; the other 9 languages are derived assets,
  **consolidated 2026-06-26 from the old per-area
  `locales/<lang>/<area>.json` fragments (90 files → 10)**. Adding/extending a
  language edits that one file. **Gotcha:** Vite's glob set is captured at
  dev-server start; adding a *new* locale file while the server runs needs a
  **fresh** restart (not just HMR) or the new keys render as raw keys.
- **`SUPPORTED_LANGUAGES`** is the homepage's 10-language set (en/zh/ja/ko/
  es/fr/de/ru/pt/ar) with `code`/`label`/`flag`; [[LanguageToggle]] renders
  straight off it.
- **RTL.** `RTL_LANGUAGES = ['ar']`; `applyDocumentLang()` sets
  `<html lang>` + `dir` on init and on every `languageChanged`.
- Persisted under `LANG_STORAGE_KEY` ('nx_lang') via the browser language
  detector. Tests import this module (see `test-setup.ts`) so specs resolve
  real strings.

Plural-category counts differ per language (ar 6, ru 4, CJK 1, en 2) so leaf
key *counts* differ across locales, but the base-key sets are identical.
