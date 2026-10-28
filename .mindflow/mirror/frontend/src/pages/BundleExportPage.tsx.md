---
code_file: frontend/src/pages/BundleExportPage.tsx
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 — `embedded` 模式(内嵌进 Dashboard 导出标签页)

新增 `embedded?: boolean` prop(默认 false=独立 /app/bundle/export 路由)。
embedded=true 时:**整个标题簇(返回箭头 + Package + h1)都不渲染**(只留右侧
summary),否则和 Dashboard 的「智能体管理」h1 叠成两级标题;页脚 Cancel 换成占位
`<span/>`;导出成功也不离页——页面 chrome 由左侧标签栏承担。

独立路由的返回/取消/成功不再去 `/app/settings`(review 指出:导出入口已从 Settings
移走,回那儿会把用户丢在一个没有导出入口的页)。改走 `exitStandalone()`:该路由
现在唯一入口是 [[TeamDetailPage]] 的团队快捷导出,所以 `navigate(-1)` 回到来处;
`location.key === 'default'`(直接粘 URL 打开、无 history)时兜底到
`/app/dashboard?tab=export`。返回箭头 aria-label 相应从 `backToSettings`
换成中性的 `pages.bundleExport.back`(「返回/Back」,10 locale)——按钮不再去
Settings,读屏名不能再说 back-to-settings。header/footer 的占位 `<span/>`
换成容器 `justify-end`。测试:bundleExportEmbedded.test.tsx(标题簇有无 + 冷开/
有来处两条 exit 分支)。


## 2026-08-11 — 渐进披露:选完才出现区块;打包信息进确认弹窗;全局开关上移

Owner 对着截图的三点重构(信息架构,导出语义零变化):

1. **底部固定块(文件名 + Notes + 两个 checkbox,~300px)整体删除**——它把
   滚动区压得几乎不可用。文件名/Notes 挪进 ReviewSummaryModal(可编辑,
   `onFilenameChange`/`onIntroChange` 回写页面 state),打包细节在导出确认
   时刻填写。
2. **两个全局开关上移到 MODE 行下方**(它们改变下方各区的内容,放最底部
   是倒果为因)。凭据项文案去掉 "Full mode" 字样(与 MODE 单选的
   Full snapshot 撞名,实际语义是 include_channel_credentials +
   include_skill_secrets)——10 个 locale 同步,其中 8 个原本缺这个 key
   一直 fallback 英文,顺手补齐。
3. **渐进披露**:`hasSelection`(agent kind 看 selectedAgents,team kind 看
   selectedTeam)为假时,history/skills/social/bus/artifacts/workspace 六个
   区块不渲染,只显示 `pickFirstHint` 一行提示;选中后才展开全部区块。


## 2026-08-06 (3) — Chooser 统一 + Agent bundle 收紧为单选

Owner 三连反馈的修复:
1. **四张选择卡完全同构**:Bundle(agent/team)与 Mode(full/custom)
   共用 ChoiceCard(同 padding/min-h/圆角;选中 = border-strong +
   nm-card 白底,未选 = hairline + nm-paper,hover paper-warm),
   ChooserRow 统一标签列宽。
2. **Agent bundle = 恰好一个 Agent**(radio 式点击替换选择;切 kind 时
   仅保留第一个已选)。多 agent 非团队导出能力随之移除 — 语义换清晰度,
   Owner 认为多选有误导性。AgentsTab 的引导段落、per-team quick-add
   chips、All/Clear 全部删除(团队整组打包是 Team bundle 的职责),
   toggleAgent 与相关 i18n key 清掉。
3. 残余深浅:agent 卡片改 nm-paper/nm-card 词汇,fullNote 用
   --color-warning。

## 2026-08-06 (2) — Agent/Team bundle 单选卡落地 + 色板统一

Owner 拍板(此前挂 todo):header 下新增 **Bundle 类型单选卡**(第一道
分叉,mode 卡之前)。agent kind = 多选网格 + per-team quick-add chips
(team 下拉隐藏);team kind = 下拉即选择(handleSetTeam 把该团队的
live 成员整组替换进 selectedAgents,成员卡只读展示 — v4 语义「成员
自动随行」),?team= 深链自动切到 team kind。切回 agent kind 清空
selectedTeam。

色板统一(「一会深一会浅」修复):全页扫除 legacy 灰阶
bg-secondary(nm-bg2 偏深米)/bg-tertiary/bg-elevated/bg-sunken,
统一为 nm-paper / nm-card / nm-raised / nm-paper-warm 词汇;条带底
一律 nm-paper,可点卡片 nm-card,hover nm-paper-warm。
todo/2026-08-06-export-bundle-kind-radio.md 已闭环。

## 2026-08-06 — Chat UI v4:七个 tab 改为可折叠分区

排他 tab 条删除,七个 scope(agents/history/skills/social/bus/artifacts/
workspace)变为同一滚动流里的折叠分区(openScopes: Set,可多开,默认开
agents;ScopeHeader = chevron+icon+label)。**内容组件零改动** — 只是换
容器;mode 卡、footer 表单、Review Summary 强制步骤、各 scope 的默认勾选
极性(events/MCP opt-in,narratives/artifacts/bus opt-out)、
?team=&agents= 深链全部原样。v4 mock 里的 Agent/Team bundle 单选卡未做:
AgentsTab 内部现成的 team 下拉 + quick-add chips 已承担同一职责,再加一层
单选卡是重复控件 — 记入 self_notebook/todo 待 Owner 裁决。

## 2026-08-17 — SEC-07：不再回传归档路径

zip 方式的 `SkillExportSpec` 原先带 `archive_path`（从
`GET /skills/archives` 读到再原样回传）和 `manual_zip_path`。后端已把这两
个字段删掉——客户端提供的路径是任意文件读的入口（见 [[bundle.py]]），现在
由 builder 自己按 user 查 `skill_archives`。

前端只剩**展示**用途：`hasZip` 判断有没有归档、`archivePrefix` 显示
basename。`manual_zip_path` 全前端从来没有任何地方赋值过，一并删除；
`manualPrefix` 这个 i18n key 仍被 url 方式那张卡用着，保留。

## 2026-07-13 — full-mode checkbox also carries skill secrets

The existing 'include credentials' checkbox now sends both `include_channel_credentials` and `include_skill_secrets` (one 'full mode' opt-in), and the warning text covers skill secrets (env_config + skill credential files).

## 2026-07-13 — opt-in channel-credential export

Added an opt-in `include_channel_credentials` checkbox + a strong plaintext-secret warning. Off by default; when on, the request ships IM channel credentials so a migrated agent's channels work without re-binding.

# BundleExportPage.tsx — Export wizard (subproject 2 §8.13)

7 tabs（Agents / Chat history / Skills & MCP / Social Network / Message Bus / Artifacts / Workspace files）+ Bundle Notes editor + Review Summary modal。

## Tab data flow

1. **Agents tab**：选 agent_ids + （可选）team。其他 tab 的内容范围跟随这里。
2. **Chat history tab**：narrative / event / job 三层勾选；放进同一棵树是因为这三者有 narrative 的 cascade 关系。
3. **Skills & MCP tab**（2026-05-15 改名）：上半段沿用 SkillsTab（url / zip / full_copy / skip）。下半段 `McpSection` 列每个 agent 的 `mcp_urls`，**默认全不勾**（opt-in 设计：MCP URL 经常指私网，bundle 1.1 起 import 时会直接 write-through 到接收方 mcp_urls，不该意外泄露）。数据走 `api.previewMcps(agent_ids)`。
4. **Social Network tab**：双栏 + 分页 + accordion。匹配同 team 名字的默认勾选（最宽匹配规则，议题 7.f.1 接受误报）。
5. **Message Bus tab**：调 `api.previewBusChannels(agent_ids)` 拿候选 channel（owner==self AND ≥1 closure 成员）；用户可勾掉某些 channel。默认全选，与旧版 closure-auto 行为对齐。Full mode 强制全选 + 只读。
6. **Artifacts tab**（2026-05-15 新增）：调 `api.previewArtifacts(agent_ids)` 列每个 agent 的 `instance_artifacts`。默认全选，可单独排除。文案明确告诉用户：底层文件总跟 `workspace.tar.gz` 走，这里只控制 DB 指针行是否入包；接收方导入时 session_id 会被清掉、pinned 强制 1。
7. **Workspace files tab**：从 `api.listFiles` 拿文件列表，sensitive pattern 命中默认 unchecked + warning 标。

## Review Summary modal

强制最后一步（议题 6.7.a-A）。列出 included / stripped / warnings 三块，含"未自动扫描自由文本"的告知（议题 6.5）。

## Gotcha

- `listSkills` / `getSocialNetworkList` / `listFiles` 这些 backend endpoint 的实际返回字段名我没 100% 对齐（A3/A4 不确定项），可能在某些 tab 显示空 → 测试时若空就是字段名不对，调对齐即可。
- `RadioCard` 默认勾选逻辑写在 `useEffect([JSON.stringify(skillsForAgents), ...])`，对深嵌套对象用 stringify 兜底；性能足够。
