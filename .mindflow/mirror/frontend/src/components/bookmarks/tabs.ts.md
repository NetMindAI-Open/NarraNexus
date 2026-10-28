---
code_file: frontend/src/components/bookmarks/tabs.ts
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — `visibleCategories(ctx)` 回归(抽屉标题切换器恢复)

抽屉标题切换器按 Owner 要求恢复([[BookmarkDrawer]] 同日条),它按分类分组列面板,所以
重新提供 `visibleCategories(ctx)`:`stripCategories()` 每组用同一个 `tabOffered` 过滤、
丢弃空组。可见性规则仍只有 `tabOffered` 一处,且只有**一个出口**:`visibleCategories` 施加它,
`visibleTabs(ctx)` 就是 `visibleCategories(ctx).flatMap(c => c.tabs)`(不再各自一条过滤链;
因 `allTabs()` 本身即 `stripCategories().flatMap`,返回值与顺序不变)——切换器、⋯ 菜单、⌘K
永远一致(无 studio 的 agent 不会在任何一处看到 `builder`)。将来规则升级为跨组规则时只改一处。
下方 09-04「`visibleCategories` 删除」条已作废。

## 2026-09-07 — `STRIP_CATEGORIES`/`ALL_TABS`/`BUILTIN_TAB_IDS` become functions (I-3, import-order hazard)

Renamed to `stripCategories()` / `allTabs()` / `builtinTabIds()` and changed from eager
module-level `const`s to functions that compute their result fresh from `PANELS.list()` on every
call. This was forced by a real import-order bug, not a style preference: `platform/builtin.ts`
imports `ArtifactsGlyph` from this file BEFORE `builtin.ts`'s own `PANELS.register(...)` calls run
(for the artifacts panel's strip icon). If this file had computed the strip tables as eager
consts, that computation would run against an EMPTY `PANELS` registry and freeze there forever —
a JS module-level const only evaluates once, at first import, and `tabs.ts` is guaranteed to be
imported (for the icon) before `builtin.ts` finishes registering anything. Every consumer
(`ChatHeader.tsx`, `bookmarks/index.ts`, `builtin.test.ts`, `builderTab.test.tsx`) was updated to
call the functions instead of reading former consts. `CATEGORY_META`/`CATEGORY_ORDER` stay small
static tables (category branding is shell IA, not per-panel data, so there is no import-order
risk for them). `AtomicTabId` narrowed from `BuiltinTabId | (string & {})` to plain `string`
(the type no longer usefully distinguishes "known builtin id" once tab ids can come from any
plugin's `PANELS.register` call). See `platform/registries/panels.ts`'s mirror doc for the
`PanelStripDef` shape this reads, and `platform/builtin.ts`'s mirror doc for the other side of
the import-order dependency.

## 2026-09-04 (六轮) — `conditional` 说明书里最后一句改对

「`STRIP_CATEGORIES` 的新消费者自动继承规则」是反的：`STRIP_CATEGORIES` / `ALL_TABS` 是未过滤
的注册表（ChatHeader 的 `ALL_TAB_DEFS` 就拿全量表按 id 取 def），规则**只**由 `visibleTabs(ctx)`
施加。新面板入口必须走 `visibleTabs`，否则会把 `builder` 提供给每个 agent——⌘K 那一轮踩过。

## 2026-09-04 — `visibleCategories` 删除（五轮真正落地；四轮那条 commit 里脚本中断未生效）

dev（#383）退役了抽屉切换器，分组形态再无生产消费者；只留扁平的 `visibleTabs`，消费方是
ChatHeader ⋯ 菜单与 ⌘K。

## 2026-09-04 — `conditional: 'studio'` 也在「可恢复」时提供

`TabVisibilityContext` 多 `studioResumable`；`tabOffered` = open || resumable。条件仍然落在
「这个 agent 走过 AI 创建路径且没按完成」上，不是「所有 agent 都能开 studio」。

## 2026-09-03 (评审修订) — 可见性规则收口到注册表

`builder` 曾只在两个消费点被过滤（MainLayout 的切换器、ChatHeader 的硬编码菜单），
而 `ALL_TABS` 这条派生链没被扫到：⌘K 面板（移动端主入口）对任意 agent 都列出
Builder。现在 `AtomicTabDef.conditional?: 'studio'` 是**唯一**的规则所在，
`visibleTabs(ctx)` 是所有「可选列表」的出口（`visibleCategories` 已随抽屉切换器删除）——
切换器与 palette 都调它，新消费者自动继承。`ALL_TABS` / `tabLabelKey` /
`tabDescKey` 仍含 `builder`：注册表不过滤，否则已经停在这个 tab 上的抽屉标题会回
落成 `rail.builder` 字面量。测试 `builderTab.test.tsx` 钉住「关 studio 时
visibleTabs 恰好少这一个」。

## 2026-09-03 — 新增 `builder` 原子 tab

创建工作室的配置面板。放在 Config 组**首位** —— 它是配置的「从这里开始」：一段
把其余 tab 填好的对话。遵守既有 IA（一个 tab 一个 panel），没有嵌套堆叠。

## 2026-08-06 (2) — tabDescKey:每个面板一句话说明

新增 `tabDescKey(id)` → `rail.desc.<id>` 约定(11 个面板 × 10 语言)。
消费方:BookmarkDrawer 头部的 ? 圆圈(hover 气泡)、ChatHeader ⋯ 菜单项
的 title。目的:不看文档的用户也能知道 Awareness / Channels / MCP 等
是干什么的(Owner 2026-08-06)。文案讲「用处 + 使用逻辑」各一句,
新增面板时必须同步补 10 语言的 desc key。

## 2026-08-06 — artifacts 成为原子 tab + 自定义 glyph

新增 `artifacts` AtomicTabId(Activity 类目,Jobs/Inbox 之后),面板 =
ArtifactColumn(forceExpanded)走 BookmarkPanelHost。图标是本文件导出的
**ArtifactsGlyph**(圆点-连线-方块,Owner 2026-08-06 截图指定;lucide 无
此形,本地按 lucide 笔画约定手绘,cast 成 LucideIcon 供注册表/palette/
ChatHeader 混用)。deriveTabStatus 对 artifacts 走默认 none — 头部徽标
数来自 artifactStore,不经 bookmark 信号。

## 2026-06-20 — MCP strip caption shortened

The `mcp` tab gained `stripLabel: 'MCP'` so the strip caption reads "MCP"
instead of the truncated "MCP SERVE…". The full `label` ("MCP Servers") is kept
for the tooltip / aria-label.

## 2026-06-11 (PM)

`stripLabel` optional short caption for the 64px strip (Social
Network → "Social").



# tabs.ts — the strip as a PROJECTION of the panel registry

本文件**不再是 tab 的来源**（2026-06-11 那版才是，改到今天已经反过来了）。
tab 从哪来：每条 `PANELS` 注册项自带 `strip` 元数据（label / labelKey / icon /
stripLabel / order / category / conditional），`tabDefFor(entry)` 把它投影成一个
`AtomicTabDef`，`stripCategories()` 再按 `strip.order` 排序、按 `strip.category`
分组。**没有 `ALL_TAB_DEFS` 这个东西**，也没有任何一张写死 id 的表。

因此「加一个 tab」= 在自己的插件（或 `platform/builtin.ts`）里
`PANELS.register(id, { component, strip: {...} })` **一处**。
[[BookmarkPanelHost]] 里也没有 render 分支可加——它是纯查表
（`PANELS.get(tab)?.component`），这正是当年那句「一个 tab 一个 panel、绝不堆栈」
的 IA 在结构上被兑现的样子：面板组件和它的入口是同一条注册记录的两半，不可能只落一半。

本文件今天真正拥有的东西只有三样：

- **类目品牌表** `CATEGORY_ORDER` / `CATEGORY_META`（config / activity / narra /
  nexus / skills）。这是 shell 的信息架构，不是某块面板的属性，所以不下放给注册项；
  注册项只用 key 认领类目，认不出来的 key 回落 `config`。
- **可见性规则的唯一出口** `visibleTabs(ctx)`。`stripCategories()` / `allTabs()`
  故意**不过滤**——按 id 反查 def、解析抽屉标题都要拿到全量表，一旦在这里过滤，
  已经停在 `builder` 上的抽屉标题会掉回 `rail.builder` 字面量。所有「让用户挑」
  的入口（ChatHeader ⋯ 菜单、⌘K palette）必须走 `visibleTabs`，绕过它就会把
  `builder` 发给每个 agent（⌘K 那一轮的原始 bug）。
- **bookmarkStore 信号 → tab 状态** 的映射（`deriveTabStatus` / `markTabOpened`）。
  jobs 是 failedJobs 徽标 > running 转圈 > info 点，inbox 是未读徽标，awareness 是
  外部更新 info 点，其余 `none`。attention / 徽标只在底层条件解除时消失，
  `markTabOpened` **只**清 info 档——「点开过」不等于「处理过」。

外加一个本地图标 `ArtifactsGlyph`（lucide 没有这个形，按其笔画约定手绘，cast 成
`LucideIcon` 供注册表/palette/header 混用）。它正是把上面三张表做成**函数**而非
模块级常量的原因：`platform/builtin.ts` 为了拿这个图标会先 import 本文件，此时
`PANELS` 还是空的，任何 eager const 都会永久冻结在空表上。
