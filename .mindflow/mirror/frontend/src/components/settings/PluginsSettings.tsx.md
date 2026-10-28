---
code_file: frontend/src/components/settings/PluginsSettings.tsx
last_verified: 2026-08-28
stub: false
---

# PluginsSettings.tsx — Settings › Plugins panel

## 为什么存在

轻量化插件化后 Claude Code / Codex CLI 不再随桌面镜像预装，改成用户按需
安装的本地插件（后端编排在 `backend/integrations/plugins`）。这个面板是
唯一的安装入口——[[ModelDefaultsSettings]] 和 [[AgentLlmConfigPanel]] 的框架
下拉只负责"禁用未装的选项 + 指路过来"，真正装/卸/更新的动作都在这里。独立
成文件而不是塞进 `ProviderSettings`：插件安装是"要不要有这个 CLI 能力"，
provider 钱包是"这个能力用谁的 key 跑"，两件事的失败模式和操作节奏都不同
（前者分钟级、走 pip/npm；后者是表单提交）。

## 上下游关系

- **被谁用**：[[SettingsPage]] 的 `plugins` nav 项直接渲染它，是自包含的
  受控面板（自己 fetch、自己管 loading/busy 状态），父级不传 props。
- **依赖谁**：`api.getPlugins` / `api.installPlugin` / `api.uninstallPlugin`
  （见 [[api]]）；UI 原语来自 `components/nm`（`PaperCard` / `Button` /
  `Spinner` / `StatusBadge`），遵循 `design_system.md` §6 的"新界面先查
  nm/"规则。

## 设计决策

- **安装日志内联渲染，不用 toast/modal**：pip/npm 装包动辄几十秒到几分钟，
  没有可见进度时那段等待读起来像卡死。`api.installPlugin` 的 ndjson 回调
  逐行追加进卡片内的小滚动区（`data-testid="plugin-install-log-<id>"`），
  只保留最近 `MAX_LOG_LINES` 行防止无界增长。
- **更新 = 重新调用 install**：没有单独的"update"端点——目标版本已经由
  后端在 `PluginStatus.target_version` 里解出，更新按钮只是带着
  `update_available=true` 的同一个 `runInstall`。
- **`cloud_managed` 时整个组件返回 `null`**：云端集中管理这些插件，本地
  装/卸载按钮点了也只会撞上后端 403；与其显示一个禁用的假面板，不如让这
  个 nav 项在云端直接是空白（[[SettingsPage]] 的 2026-08-28 条目有同样的
  取舍记录）。

## Gotcha / 边界情况

- **触发**：某个插件正在安装时 `busy` 同时来自本地 `busyIds`（本次操作发起
  的乐观状态）和服务端 `PluginStatus.busy`（另一个标签页/会话在装）→
  **症状**：按钮在没点过的情况下也会被禁用 → **根因**：两者用 `||` 合并
  （`busyIds.has(p.id) || p.busy`）是故意的——单机唯一安装目录意味着并发
  安装两次会互相踩，禁用面收窄成"服务端说忙就不能点"更安全，不能只信本地
  状态。

## 相关约束
- `.mindflow/project/references/design_system.md` §6（组件选型决策表）——
  新面板全部走 `components/nm`。

## 2026-08-28 补(auto-review I-new-3) — version=null 不再渲染 vnull + 给修复按钮

N8 后端语义(文件在但版本读不到→installed=True,version=null)会让徽章渲染字面量 `vnull` 且只剩 Uninstall(死胡同)。修:徽章 version 为 null 时显示 versionUnknown(warning 色);Update 按钮条件放宽成 `installed && (update_available || version===null)`(重装语义)。i18n 加 versionUnknown(en+zh)。测试补 installed:true+version:null 用例。
