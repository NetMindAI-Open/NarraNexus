---
code_file: frontend/src/components/chat/team/TeamWorkspacePanel.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 注释跟随:标题切换器恢复

`tab` prop 的所有者改述为「member bar toggle 与抽屉标题切换器」;外壳注释改回「title switcher」。
行为不变。见 [[../../bookmarks/BookmarkDrawer]] 同日条。

## 2026-09-03 — 「一个面板两个 tab」的入口回到 member bar

组件本身零改动(仍是受控 `tab: 'artifacts'|'files'` + 列表/查看器)。变的是
上游:抽屉标题下拉退役后([[../../bookmarks/BookmarkDrawer]]),两个 tab 各自
由 member bar 的 `artifacts-toggle` / `files-toggle` 打开。§5.2 的「一个工作台
两种模式」没有回退成两个面板——两种模式共用同一套列表+查看器布局,预览区
与当前 tab 无关(芯片选中不切 tab,见 2026-08-07 条目),这一点正是它仍然是
**一个**面板的原因。文件头注释同步(不再写「壳含标题切换」)。

## 2026-08-19(二)— 列表让位查看器

master 列表 w-64 shrink-0 → w-40 min-w-[7rem] shrink:抽屉默认 400px
(与单聊共享的偏好)时查看器只剩 ~143px。列表是选择器不是主角,让位后
默认宽度下查看器 ~239px 起步,拖宽随用户。**不许**为 artifacts 单独放大
pinnedWidth——宽度偏好与单聊共享,写回会拽动单聊。

## 2026-08-19 — 纯内容化:壳归抽屉

外层定位/宽度、内部 tab 条、关闭钮全部移除——壳(标题切换/钉选/关闭/
宽度)归共享 BookmarkDrawer,本组件收 `tab: 'artifacts'|'files'` 受控 prop
只渲染列表+查看器。onClose prop 删除。

## 2026-08-19 — 残余英文清零 + 相对时间共享化

- Artifacts/Files 两个 tab 标签、两句空态 hint、Download title、Loading
  全部改走 `chat.team.workspace.*`(10 locale)——上一批只覆盖了
  close/zoom/viewer 空态,这批把面板内最后的硬编码英文清零。
- 本地 `formatWhen`(英文写死)删除,改用 [[utils]] 的 `formatMessageAge`
  (Intl.RelativeTimeFormat,全语种免费)。[[ArtifactsSection]] 的同款
  本地副本同批清除。
- **xl+ 从 absolute 悬浮改 in-flow 列**(聊天让位不被盖),宽度
  `min(50vw, 760px, 100vw−928px)`——928 = 侧栏 272 + 聊天最小 400 +
  roster 静息 256,与 [[../../layout/drawerLayout]] 同一 reserve 思路,
  否则 shrink-0 列在笔记本宽度把聊天挤到 0。xl 以下没有可分的空间,
  保持 overlay(手机全宽)。tab 与列表行挂 row-hover/row-active 选择台阶
  (design_system.md §2.5——hairline 是线色,不再当填充用)。

## 2026-08-19 — 6 处硬编码英文接入 i18n

close/zoom 的 title+aria、两句空态文案全部改走 `chat.team.workspace.*`
(10 locale 同步);zoom 文案复用各语言 `artifacts.zoom` 的既有译法,不另造。
组件顶层加 `useTranslation`(它被 TeamChatPanel 条件渲染,hook 在顶层即可)。

## 2026-08-18 (二次) — 常驻窄列 → 顶栏开合的大抽屉

第一版修法(角落盒 + zoom 钮)Owner 否了:速览盒根本起不到预览作用。现在
与单聊 artifacts 完全同构:入口是房间顶栏的 ArtifactsGlyph 按钮(带计数,
消息下的 artifact 芯片点开也会弹出),面板是 `min(50vw,760px)` 的右侧覆盖
抽屉——左列表(w-64)+ 右满高 ArtifactRenderer 查看器,Maximize2 仍可进
全屏 zoom。常驻 w-72 列和 h-64 角落预览删除;`onClose` 归抽屉。

## 2026-08-18 — 选中 artifact 可全屏查看(复用 ArtifactZoomModal)

右下角 288×256 预览盒装不下一个真 artifact(Owner 对照实屏)。角落盒保留
作速览,预览头部新增 Maximize2 放大钮 → 单聊同款全屏查看器。

## 2026-08-10 (review 修正) — Files 页可下载（验收 #5 的缺口）

初版 Files 那半边每行是纯 `<div>`：名字/分享者/时间/大小，**没有任何下载入口**。
Artifacts 那半边可点开内联渲染，Files 是死文本——验收 #5 的「可下载」当时被我打早了勾。

**不能用 `<a href>`**：`GET /api/agent-inbox/attachments/raw` 是 JWT / X-User-Id 门禁的，
浏览器导航两个都不带。复用既有的 `api.fetchBusAttachmentBlob`（[[useBusAttachmentBlobUrl.ts]]
已在用同一条路），取到字节后走临时 object URL——**不新增第二条需要加固的下载路由**。

端点本身天然安全：`resolve_shared_file_for_user` 按**调用者自己的 root** 解析，被篡改的
path 最多够到调用者本就拥有的文件。

锚点要**挂进 document**、object URL 要**延后一拍再 revoke**：Firefox 会在 URL 于同一个 task
内失效时掐断大文件下载，游离锚点在那里也不可靠。

## 2026-08-07 (三次) — 改为纯展示组件，选中态由父级控制

数据获取上提到 [[TeamChatPanel.tsx]]。原因：消息下的芯片和面板列表**必须对「现在打开的是
什么」有一致认知**，所以状态只能有一个所有者，而它必须是两者共同的父级。两个组件各自拉同一份
数据也是重复请求。

预览窗格在列表**下方的独立区域**，因此芯片选中时**不切 tab**——切了会把用户从 Files 浏览中
拽走，而预览本来就与当前 tab 无关。（初版用 effect 强制切 tab，被 lint 的
`set-state-in-effect` 挡下，顺着提示发现那个同步本身是多余的。）

## 2026-08-07 (二次) — 改为面板内联渲染，并发现团队 view-token 是多余的

初版点击 artifact 走 `window.open` 新标签页。改为在面板下半部内联渲染
（复用 [[ArtifactRenderer.tsx]]）——这个面板存在的意义就是**边读对话边看产出**，新开标签页
恰好把那个上下文丢掉。

**关键发现**：`ArtifactRenderer` 零 store 耦合，且内部按 `artifact.agent_id`（**产出者**）走
agent 侧 view-token。而 agent 路由的鉴权是「JWT 用户是否拥有该 agent」——团队成员本来就都是
团队 owner 的 agent，所以**队友的 artifact 用既有路由就能取到**，无需任何团队专用 token 通道。

因此 `POST /teams/{id}/artifacts/{id}/view-token`（commit 6209dba5）在当前 UI 路径上**用不到**。
保留与否见该 commit 的说明与 owner 决定；它表达的是「按 team 归属授权」这一更贴切的语义，
但功能上与 agent 路由重叠。

# TeamWorkspacePanel — 团队房间的工作台

## 为什么存在

团队的产出此前无处安放：team turn 注册的 artifact 只出现在**产出者的私聊**里，想看「团队做了
什么」得离开房间、点进某个成员的一对一会话；共享文件则完全没有 UI，找一个文件只能靠 agent 在
聊天里念绝对路径。

## 关键设计

**一个面板两个 tab，而不是两个入口**（§5.2 定案）。心智模型是「团队的工作台」；拆成两处等于
要求用户**先知道自己要找的是哪一类东西**才能开始找。

**每一行都标注归属。**私聊里「这是谁做的」从来不是问题；团队房间里多个 agent 写进同一个空间，
不标注的列表就是共享工作台退化成匿名堆放的方式。

**两个 tab 一起加载**：计数显示在 tab 标签上，懒加载会让未打开的那个 tab 谎称有 0 项。

**打开 artifact 走团队 view-token**（[[teams.py]]），不是 agent 侧那条——后者要求调用方 agent
**就是**产出者，在团队里恰好是反的。

**挂在 transcript 旁边而非独立路由**：「我们做了什么」是**边读对话边问**的问题。
`refreshKey` 取消息数，使得产出 artifact 的那一轮无需用户手动刷新即可显现。


## 2026-08-18 — 已退役工具名的跟随

`bus_share_to_team` → `team_share_file`（用户可见的提示文案，此前指向一个不存在的工具名）、
`send_message_to_user_directly` → `reply_owner` / `notify_owner`。后者在前端不只是措辞：
按工具名挑气泡内容的三处只匹配旧名字时，回复是真的、内容在那儿、气泡就是不渲染 —— 同一条
规则现在收在 `lib/ownerTools.ts`，镜像见 [[ownerTools.ts]]。
