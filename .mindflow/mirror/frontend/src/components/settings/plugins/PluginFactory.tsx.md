---
code_file: frontend/src/components/settings/plugins/PluginFactory.tsx
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — builtin rows the distribution excludes

A builtin row with `excluded: true` shows a neutral "not in this distribution" badge and no enable/disable/install-deps
controls and no "protected" badge ("cannot be disabled" is moot for a plugin that is not loaded) (the backend refuses them with 400 anyway; the button would only produce an error toast). New i18n key
`pages.settings.plugins.factory.state.excluded` exists in all 10 locale files.

## 2026-09-07 — the disclosure survives a reload, and covers frontend-bundle risk too (I-12)

Two gaps in the "unavoidable disclosure" flow (spec §10.4): (1) the pending-ack state was a
single in-memory `pendingAck`, lost on any reload — a user who dismissed the tab mid-review (or
the tab crashed) before clicking "I understand" got the plugin silently left disabled with no
prompt to ever re-surface it; (2) the disclosure only fired for plugins declaring backend
`permissions`, but a plugin whose manifest ships a `frontend` bundle (able to run code inside the
app UI with the user's session) carries real risk even with an empty backend `permissions` list,
and got no disclosure at all.

Fixed: `pendingAckQueue: PendingAck[]` (seeded synchronously from `localStorage`,
`PENDING_ACK_STORAGE_KEY`) replaces the single in-memory field — `load()` reconciles on every
fetch, queuing an ack for any `enabled && !permissions_acknowledged` plugin that declares backend
perms OR ships a `frontend` bundle. The disclosure dialog shows an extra bullet
(`permissionsFrontend`, added to all 10 locales) when `hasFrontend` is true. Acknowledging now
calls `api.factoryAction(id, 'acknowledge-permissions', { permissionsAcknowledged: true })`,
which persists `permissions_acknowledged: true` server-side (`api.ts`'s `factoryAction` gained a
3rd `opts` param for this) — the durable field this flow was always supposed to be reconciled
against, not just a client-side dismiss.

## 2026-09-03（批 2f.1）— 审批卡

工场页顶部列出 Agent 的待批提案（摘要/动作/范围/来自哪个 Agent/测试结果/权限清单），批准或拒绝；
超时的提案后端已标 expired 不再出现。

## 2026-09-03（批 2d.3）— 工场页（用户插件）

设置 → 插件 里框架安装器下方的第二段：列出 `/api/plugin-factory` 的插件（状态芯片按内核状态机、warning、
隔离原因、provides）；按 `owner/repo[@tag]` / `owner/repo#ref` / 本地目录安装；安装返回的 `permissions` 非空
时弹披露卡，用户「我了解」才 `acknowledge-permissions`，否则 disable（spec §10.4 首次启用第三方必须明示）；
enable/disable/upgrade/uninstall（`protected` 插件不能停用/卸载）；LKG 回滚；安全模式横幅 + 二分向导
（good/bad/stop）；每插件错误环（errorSink 上报的）。每个变更后提示「重启生效」——插件在启动时加载，页面
不假装热生效。云端整段隐藏。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

A "Built-in features" section lists the builtins with enabled/protected badges and an enable/disable toggle (hidden for protected ones) that hits `factoryBuiltinSetEnabled` and shows the restart notice.

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

A builtin with `deps_missing` shows a warning badge, the reason, and an install-retry button (`factoryBuiltinInstallDeps`).
