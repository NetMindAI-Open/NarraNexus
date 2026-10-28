---
code_file: packages/narranexus-contracts/src/narranexus/contracts/ui.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

第 18 个记录 `ToolRenderer(id)`：id 为裸工具名，镜像前端 `ToolRendererDef` 的声明式子集（组件、accepts
只存在于 bundle）。docstring「十七个」改「十八个」。

## 2026-09-07（四轮复审）— 第 17 个记录 `ArtifactKind`

前端加了 `artifactKinds` 注册表却没有 Python 侧的镜像（复审 Critical：builtin.ui 的两条包契约测试变红、插件没有声明式路径）。
`ArtifactKind(id, label, download_ext)` 是 manifest `frontend.ui.artifactKinds` 能表达的声明式子集；渲染器/编辑面/保存模式只在 bundle 里。
docstring「十六个」改「十七个」。

## 2026-09-03（批 2a）— `Theme`

`ui.themes` 位的契约：只列出要覆盖的设计 token（前端按 `@theme` 已声明集合校验），`dark` 标记配色族。

## 2026-09-03（批 1）— `ui` 位的 Python 侧契约

前端真正的贡献注册表在 TypeScript（`frontend/src/platform/registries`）；Python 侧只需要给 `ui`
扩展位一个可 import 的契约符号，让发行版能绑定自己的壳。`Shell` 是 backend 提供静态资源所需的
最小数据（构建目录 + 入口文件），纯数据、frozen，默认提供者 `builtin.ui`。

## 2026-09-07 — 前端 16 个注册表的条目形状镜像（B7）

Page/SidebarItem/Panel/SettingsSection/Command/ChannelConfig/MessageRenderer/TimelineEvent/ConversationKind/SlotComponent/SlotAction：builtin.ui 声明的 ui.* 槽的契约符号。TS 的 *Def 接口是真值；这里只承载 manifest frontend.ui 能表达的声明性子集（id/label/order/route），行为（组件、回调）只存在于 bundle。
