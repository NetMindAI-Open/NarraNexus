---
code_file: frontend/src/i18n/__tests__/localeParity.test.ts
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

扫描清单去掉已删除的审批组件、加入 `BrowserNotices`；锚点 key 改为 `browser.notices.loadFailed`。

## 2026-09-23 多页面支持

BrowserPageTabs 纳入 AST 键扫描；十种语言同步标签、关闭、跟随、空白页和 Agent 标记。

# Locale completeness and browser UI coverage

English defines the locale key contract, with Chinese kept in full parity.
Other languages must preserve complete namespaces while unrelated historical
translation gaps remain outside this gate. Browser approval, runtime settings
and the settings section labels are complete namespaces across all ten locales.

Browser UI calls are parsed with the TypeScript AST so a key missing from every
locale, including English, cannot escape a parity-only check. Every literal
browser translation used by the panel, approvals, settings and navigation must
be nonempty in all ten languages, and interpolation variables must agree with
English. Components' inline fallbacks must not conceal a missing translation.

手动安装与 BrowserScriptPermissions 同样纳入 AST 键检查，新增内容不能仅靠英文默认值通过。
十种语言均删除网站访问和例外规则文案，保留独立脚本设置及能力审批。
