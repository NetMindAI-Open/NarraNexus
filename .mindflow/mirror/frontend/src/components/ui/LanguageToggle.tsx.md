---
code_file: frontend/src/components/ui/LanguageToggle.tsx
last_verified: 2026-08-12
stub: false
---

## 2026-08-12 — PR #284 review 轮

直接写透移除——持久化改挂 i18n languageChanged 事件([[replyLanguageSync.ts]]),任何改语言路径都落库。

## 2026-08-11 — reply_language:回复语言偏好落库并注入 system prompt

select 时 fire-and-forget `api.setReplyLanguage(code)` 写透(UI 切换绝不阻塞网络;失败静默——下次切换重写)。此前只调 i18n.changeLanguage,模型无感知。

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
