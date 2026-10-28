---
code_file: frontend/src/platform/registries/themes.ts
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — url() block made case/whitespace-insensitive (M-4)

The `url(` substring check was case-sensitive and required an immediately-adjacent paren.
`URL(...)` / `Url (...)` parse identically to `url(...)` in CSS, and a protocol-relative resource
(`//host/path`, no colon) already stays inside `SAFE_VALUE`'s allowed character set — so
`URL(//evil.example/pixel.png)` walked straight through both checks, letting a plugin theme token
fire a same-request tracking-pixel/exfil load. Replaced with `HAS_URL_FUNCTION = /url\s*\(/i`.

`themes.test.ts`'s "applies and clears" test registered `acme.dark` in the module-level `THEMES`
registry and never disposed it (M-12) — a re-run of the same test, or another suite reusing that
id without vitest's per-file module isolation, would throw `RegistryConflictError` instead of
exercising the intended behavior. The test now captures and calls the disposer returned by
`register()` and asserts `THEMES.has('acme.dark')` is false afterward.

## 2026-09-07 — `THEMES` uses `Registry`'s `validate` option, no longer a `ThemeRegistry` subclass (M-11)

`class ThemeRegistry extends Registry<ThemeDef> { override register(...) {...} }` is gone.
`THEMES` is now a plain `new Registry<ThemeDef>('ui.themes', { validate: (value) => {...} })` —
see `registry.ts`'s mirror doc for why (unifying with `slotPoints.ts`'s equivalent-but-different
pattern). The thrown message dropped the `id` prefix (`ui.themes: "${id}": ...` → `ui.themes:
...`) since `validate` only receives the value, not the id — no test asserted on the id being
present in the message.

## 2026-09-07 — `applyTheme` uses `THEMES.getOrThrow(id)` (architecture E3(b))

Replaced a hand-rolled `const theme = THEMES.get(id); if (!theme) throw new Error(...)` — a theme
id reaching `applyTheme` MUST already be registered (unlike, e.g., `BookmarkPanelHost`'s
`PANELS.get(tab)`, which is a DELIBERATE fail-open: a disabled plugin's now-unregistered panel
tab renders an empty state, not an error, by design — that call site was deliberately NOT
converted). The thrown message changed from `ui.themes: "${id}" is not registered` to
`Registry.getOrThrow`'s standard `ui.themes: unknown entry "${id}"`; the affected test was
updated to match.

## 2026-09-03（批 2d）— 主题注册表

主题 = token→值 的数据；注册时逐 key 对照生成的 token 表、值做安全字符校验（禁 `url(`），主题造不出组件不读的
变量、塞不进任意 CSS。`applyTheme` 只在根元素设这些变量并记录，`clearTheme` 精确移除。
