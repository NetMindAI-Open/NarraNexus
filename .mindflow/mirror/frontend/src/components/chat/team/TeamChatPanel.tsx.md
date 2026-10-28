---
code_file: frontend/src/components/chat/team/TeamChatPanel.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 抽屉标题切换器恢复(Owner 要求)

`<BookmarkDrawer>` 重新传 `activeTab / onSelectTab / switcherCategories=teamDrawerCategories({members,
artifacts, files})`,下拉含 members / artifacts / files / manage 四项。`files-toggle` 与其它
member bar toggle **保留**,两条入口并存(注释改为「与标题切换器并存」)。见
[[../../bookmarks/BookmarkDrawer]] 同日条。roster.test 新增「标题切换器可达全部四个面板」用例。

## 2026-09-09 — root shell needs its own `min-w-0` (GitHub #131)

Phone-width (360px) send button was clipped off-screen. Root cause: the
outer `<div className="flex h-full flex-1 flex-col ...">` (the component's
own top-level flex-row child, right under `<div className="flex h-full
min-h-0">`) had **no `min-w-0`**. MainLayout's ancestor chain (`<main
className="flex-1 flex min-w-0 overflow-hidden">` → the route wrapper
`flex-1 min-w-0 ... overflow-hidden flex flex-col`) already sets `min-w-0`
+ `overflow-hidden` on ITS OWN box, so that box's rendered width is
correctly capped by upstream flexbox math regardless of what's inside — but
without `min-w-0` on the box we render *inside* it, the browser's default
flex "automatic minimum size" lets that inner box's content-based min
width win, so it silently grows past the ancestor's width. The ancestor's
`overflow-hidden` then just **clips** the excess instead of scrolling it
into view, which is why the symptom looked like the composer's
right-anchored send button (`absolute right-2`) had vanished rather than
"the page got a horizontal scrollbar."

Compare `ChatPanel.tsx`'s root: it's a `Card` with `overflow-hidden`
baked into its own className, which achieves the same "don't let content
dictate my width" effect (per the flexbox spec, an item's automatic
min-size is 0 when its own `overflow` isn't `visible` — same mechanism as
an explicit `min-w-0`, just via a different CSS property). That's the
structural reason the single-agent composer never hit this bug while the
team room did — no code shared between the two, just a missing "cap my own
size" hint.

Fix: add `min-w-0` to that inner root div (given `data-testid=
"team-room-shell"` so the regression test can pin the class without a real
layout engine — jsdom does no layout, so this can only assert the CSS
contract, not the pixel result). No downstream element needed touching:
the header row (flex-wrap + `min-w-0` on its own sub-rows, from the
2026-08-20 entry below) and the transcript pane (`min-w-0 flex-1` at its
own div) were already correct; they just couldn't do anything with a
parent that had already claimed unlimited width.

The component's outermost `<div className="flex h-full min-h-0 min-w-0">`
got `min-w-0` in the same change as a same-class guard: it is the element
MainLayout's route wrapper actually lays out, and when it sits in a
flex row its own content-based min width would reintroduce the same
overflow one level up.

Test: `TeamChatPanel.mobileLayout.test.tsx` asserts the shell's className
contains both `flex-1` and `min-w-0`, and that its parent (the outermost
root) carries `min-w-0`. The file header notes the assertions pin the
current implementation (`overflow-hidden` would be an equivalent fix).

## 2026-09-03 — 公告栏和团队管理收进抽屉第四个 tab

owner 反馈「找不到公告栏填写位置」:唯一入口是工具条最末的小按钮,设置页和空房引导都不提。
现在:①工具条的齿轮(去 `/app/teams/:id`,那页没有任何管理控件)和公告栏按钮**都拿掉**,
换成一个带文字标签的 `manage-toggle`(Settings2 + 「团队管理」,公告栏条数角标保留);
②`drawerTab === 'manage'` 渲染 [[TeamManagePanel.tsx]],公告栏 state/`reloadBulletin`/
`bulletinAction` 仍归本组件(改动会贴系统行,transcript 与面板要同源),只是往下传;
③尾部那块 `bulletinOpen` w-72 侧栏删除;④`refresh` 把响应里的 `patrol_enabled` 写进 teamsStore(`notePatrol`)——巡查开关的单一数据源;⑤新增 `handleCleared(scopes)`:清聊天→清空
messages(下一次 3s 轮询无 since 游标全量重取;`historyRefreshTick` 只有单聊面板订阅,这里不调),清文件/公告栏→`requestWorkspaceRefresh`
(原先这段逻辑在 [[../../layout/AgentList.tsx]] 的右键清理里)。
测试:`TeamChatPanel.roster.test.tsx` 「discoverable panel chrome」改断言 manage-toggle、
`bulletin-toggle` 不存在。

## 2026-09-03 — 共享文件补入口(抽屉标题下拉退役的连带)

抽屉标题不再是切换器([[../../bookmarks/BookmarkDrawer]]),`activeTab /
onSelectTab / switcherCategories` 三个 props 撤掉。**但 `files` 面板此前
只有下拉这一条入口**——member bar 只有 `members-toggle` 和 `artifacts-toggle`,
`TeamWorkspacePanel` 的 `tab='files'` 分支会变成不可达代码,共享文件功能
静默消失。所以同批新增 `files-toggle`(FolderOpen + `wsFiles.length` 计数,
与另两枚同款 aria-pressed/可见文字标签,沿用 2026-08-20 的发现性结论)。
计数从下拉项搬到 toggle 上,「入口必须宣传自己的内容」这条不变。
roster.test 新增一条用例专门钉这条入口。

## 2026-08-20 — 面板 chrome 加可见文字标签 + 组长就地指定

**公告栏/工作板「没了」的真因是发现性**：v4 改版后房间右侧的成员/工作区/公告栏三个
toggle 都是**只有 icon 的裸按钮**（只有 title/aria-label，没有可见文字），用户认不出
哪枚图标是公告栏。修复：三个 toggle（`members-toggle` / `artifacts-toggle` / bulletin）
各加**始终可见**的文字标签（成员 / 工作区 / 公告栏）。走过两个错版:①`hidden sm:inline`
在 <640px 又变回裸 icon;②只给头像条加 `min-w-0` 让宽——但 member bar 里六个子元素全
`shrink-0`,硬下限≈430px > 手机视口,而 `MainLayout` 是 `overflow-hidden`,最右的公告栏
toggle 直接被**裁掉**(比裸 icon 更糟)。终版:member bar 加 `flex-wrap gap-y-2`,并把右侧
控件(guide/工作区/设置/公告栏)包成一个 `ml-auto` 的 flex-wrap 组——桌面一字不变,手机
上整组掉到第二行、组内再 wrap,任何 toggle 都不被裁、都可点。jsdom 无 layout 测不出裁切,
所以这条只能真机核验。**没动共享抽屉的 pin 设计**——`usePinnedDrawer`
刻意让单聊与 team 房间共用 pin 键，强行解耦会跟既有架构对着干，且自动展开未 pin 的
瞬态抽屉会用整屏背板吃掉用户第一次点击（见 `drawerTab` 初始化注释）。标签化是既完整
又不违背架构的修法。

**组长就地指定**：新增 `handleSetLead`，经 `api.updateTeam({lead_agent_id})` 乐观写入
并本地 `setLeadAgentId`，作为 `onSetLead` 传给 [[TeamRosterPanel.tsx]]，让「设为组长」
落在 badge 所在的花名册里，而不是只藏在 Edit-Team 弹窗。`settingLeadRef` 拦并发点击：
否则第二次点击的 `prev` 快照会是第一次乐观写入后的值，失败回滚会退到错的中间态。守卫见
`__tests__/TeamChatPanel.roster.test.tsx`（标签可见性 + 失败回滚）。

## 2026-08-19(三)— 默认开抽屉看钉选偏好

drawerTab 的一次性初始化改为 `!isMobile && pinned` 才开 members:钉选是
与单聊共享的偏好,unpin 过的用户此前会被自动弹出的 transient 抽屉+全屏
背板吃掉进房间的第一次点击。刻意保持 initializer(非派生/非 effect)——
房间内 unpin 不得触发面板重开。切换器注册表改传
`teamDrawerCategories(counts)`(成员/可视化产物/文件计数进下拉)。「未钉选不
自动开」与「计数渲染/零隐藏」均有用例钉住(roster.test 独立 describe
「drawer defaults and switching」+ drawerPanelSwitcher 的 counts 用例);
`messagesRef` 同步改 **useLayoutEffect**——passive effect 可晚于 paint 与
事件冲刷,scroll 落进窗口读到空 ref,loadOlder 的 !cursor 静默早退无重试
(CI 满载时 dev 实测 2/20 复现,本地打补丁后 0/40);layout effect 在任何
事件可观察本次渲染前同步提交,窗口关闭。refresh/loadOlder 依赖未动
(3s 轮询不因 messages 重建)。roster 的 className 注入不再带
空转的 border-l-0(组件已无自带边框)。

## 2026-08-19(二)— 与单聊同批的三处对齐

`pinned && !isMobile`(手机不钉)、inset 宽度 320px 地板、Users2 按钮
去掉 ml-auto 归入右组;断点单一来源(useIsMobile,初始化直接用它,
删掉平行的 matchMedia 查询)。抽屉面板切换测试:roster.test 新用例
(标题下拉 members→artifacts→顶栏切回)。

## 2026-08-19 — 右侧统一为共享抽屉(成员/可视化产物/文件)

站立 roster 列、移动端 roster overlay、workspace 面板三套挂载合并为
**一个 [[../../bookmarks/BookmarkDrawer]]**:[[teamTabs]] 三面板、
[[../../../hooks/usePinnedDrawer]] 共享钉选/宽度偏好、标题下拉切换、
ResizableDivider 拖宽——与单聊右栏同一实现与语义(钉=常驻列,
非钉=桌面 in-flow 透明/手机 overlay)。桌面初始开 members(对齐旧站立
roster),手机初始关。顶栏按钮:Users2 toggle members(全视口)、
Artifacts toggle artifacts;chip 点击开 artifacts 并选中。
之前 xl 断点的自定宽度方案随之删除(抽屉自带 reserve 策略)。

## 2026-08-19 — 推理披露上移 + workspace 改 in-flow

- renderHeader 新增:agent 消息的 [[TeamMessageProcess]] 走气泡顶部插槽
  (单聊位置对齐),footer 只剩 chips+时间。
- [[TeamWorkspacePanel]] xl+ 从 absolute 悬浮改 in-flow 列——聊天左移
  让位而不是被盖住;xl 以下保留 overlay(reserve 算不开,详见彼处)。

## 2026-08-19 — 悬停提示补全

带 aria-label 但无 title 的四处控件(guide「?」开关、composer 错误条与
公告栏的两个关闭钮、房间头部的运行态气泡按钮)补上与 aria-label 同文案的
title——键盘/读屏用户本来就有名字,悬停用户现在也有。

## 2026-08-14 — 转录区认全部「非 idle」状态，不再只认 running

`TypingIndicator` → `LivenessIndicator`，过滤条件从 `status === 'running'` 放宽到
`status !== 'idle'`。

**为什么这是「群里死寂」的真正修复点**：后端一直知情——`queued` 是团队 GET 直接从
待处理消息算出来的，消息落库后**一次 3s 轮询内**就为真，完全不等 trigger；而
`running` 要等轮询间隔 + worker 槽位 + Step 0。所以此前右栏 roster 已经显示「启动中」，
左边的对话流却什么都没有。本地真机复现过这个窗口。

**没有违反两栏布局那条规则**（见 `TeamChatPanel.roster.test.tsx` 的 docstring）：规则
是「**跑完的**轮次不在流里留痕，记录在 roster 里一键可达」，不是「只有 running 才能
显示」。`idle` 仍然什么都不渲染。

**三态的视觉区分是有意的**：只有 `running` 的点会 bounce。queued/stalled 也跳动会读成
「正在工作」，那正是四态存在要防止的误读。

**颜色与文案一律取自 `STATUS_TONES`**（`lib/teamActivity.ts`）。这是第四个渲染同一组状态
的界面，而 `teamActivity.ts` 的存在理由就是「三个界面永远不会对『stalled 长什么样』产生
分歧」。第一版在这里**硬编码**了调色板，结果是同一成员同一时刻：转录区 stalled 画成
warning 琥珀、右栏 roster 画成 **error 红**；queued 则是 silicon 对 warning。两种严重度
暗示，而颜色比文字更先被看到。柔化只允许**叠在语义色之上**（queued 的 0.72 不透明度），
不允许换成另一个语义色。`TeamChatPanel.liveness.test.tsx` 里有测试断言气泡 style 含
`STATUS_TONES.stalled.color`。

**一个 i18n key 都没新增**，直接复用 roster 的
`chat.team.activity.{queued,stalled,waitingFor,silentFor}`。两个界面对同一状态说同一个
词本身就是对的，顺带省掉 10 个 locale 文件的改动。`running` 的 aria-label 逐字保持
`chat.team.typing`——那是这个元素一直以来的可访问名，也是既有测试的抓手。

**测试的坑**：断言时长文案必须 `within(bubble)` 收紧到气泡内部——roster 渲染同一个
字符串，不收紧的查询只靠 roster 就能通过，也就是说它会在「正好是本文件要抓的那个
bug」面前变绿。

## 2026-08-14 (二) — memberNameMap：一个被上游打穿的 memo

`memberNames` 此前是在 JSX 里现场 `Object.fromEntries(...)` 出来的，**每次 render 都是新
对象**。于是三层之外的 memo 全部失效：新对象 → 每个气泡的 `nameSet` 重算 → rehype 插件
数组是新的 → [[Markdown.tsx]] 的浅比较 memo 必然 miss → remark/rehype **重新解析整段正文**。

这个面板**每秒至少 render 一次**（1s ticker 让活动时长走字），打字时每个按键再 render 一
次。200 条消息的房间等于每秒把 200 段 markdown 重新 parse 一遍。

**这是上一轮修复自己引入的**：@高亮从"改写字符串"搬到"插件数组"之前，`Markdown` 的
`content` 是按**值**比较的，memo 命中；搬完之后命中条件变成了**引用相等**，而没有人让那
个引用稳定下来。三处注释（气泡里、Markdown 的 prop 文档、mirror md）都写着"必须传稳定的
数组"，只是上游没兑现。

现在 map 只算一次，两个调用点共用。`TeamChatPanel.renderCost.test.tsx` 直接钉住**身份**
（`toBe`），而不是去数解析次数——后者要伸手进 ReactMarkdown 内部。

## 2026-08-14 — transcript 外面那层 `space-y-5` 不是冗余的

review 读成了"只包着一个子元素"，实际它包着 transcript + 正在输入指示器 + 滚动锚点。
[[TeamTranscript.tsx]] 自己那层 `space-y-5` 间隔的是**消息之间**，外面这层间隔的是
**transcript 和它下面那些东西之间**。换成 Fragment 会把这个间距关掉。留了注释说明。

## 2026-08-14 — 往上翻历史，以及一个跨房间的游标 bug

房间现在打开在**最新**一页（服务端改动见 [[teams.py]]），滚到顶部请求上一页
（`before` 游标，见 [[mergeTeamMessages.ts]] 的 `beforeCursor`）。

三件容易写错的事：

- **滚动位置要自己还原。** 往前面插内容会把读者正在看的一切**下移**恰好等于新增内容的
  高度。不管 `scrollTop` 就等于把人从"让他往上翻的那条消息"旁边瞬移走——这恰恰是"加载
  更多"唯一不能做的事。
- **空页要锁住。** 到顶之后不锁，房间会在之后每一个 scroll 事件上重新问一次。
- **换房间要忘掉这个锁**，否则第二个房间会默默拒绝翻历史。

等待也要看得见：滚到顶部却什么都没发生，和"已经到房间开头了"长得一模一样。

顺带修掉一个真 bug：`refresh` 通过 ref 读 transcript，而换房间那一刻这个 ref 里还是
**上一个房间**的消息——于是新房间是带着另一段对话的 `since` 去取的。新房间里早于那个
时间戳的消息永远不会到达；如果上一个房间的最后一条更新，新房间直接渲染成空的。现在在
清空 `messages` 的同一处也清 ref。

## 2026-08-14 — 输入框补齐三件事

**草稿。** 此前是一个裸 `useState`：换房间或导航走，写了一半的内容直接消失。现在按房间
存（[[chatDrafts.ts]] 的 `getTeamDraft` / `setTeamDraft`），防抖 400ms + 切换/卸载时
立即 flush。

这里比私聊那边麻烦一点：私聊靠 `key={agentId}` 让 `<Composer>` 重挂载，房间**不重挂载**
——路由参数变了，组件还是同一个实例，`teamId` 和 `text` 在**不同的 commit** 更新。所以
面板持有 `draftRoomRef`：这段文字属于哪个房间。少了它，切房间后的第一次保存会把上一个
房间的话记在新房间名下，用户在错误的地方找到自己写的东西。

**IME。** 房间的 textarea 之前**没有输入法保护**：按 Enter 选拼音/假名候选词，直接把
消息发出去了。对这个项目实际使用的语言来说，这不是边缘情况，是输入框对一整类输入是坏的。
标志位不够——它在一个宏任务里被清掉，而某些输入法**先**发 compositionend **再**发那次
keydown，所以还需要 100ms 的宽限窗口。两者都是私聊 [[Composer.tsx]] 早就踩出来的。

**失败要看得见。** 发送失败原本静默地把文字放回去——这和"回车没生效"无法区分，用户于是
重打一遍，或者发两次。上传失败原本注释写着"静默，用户可以重试"，这句话假设用户知道有
东西需要重试；而 `success: false` 的拒绝连异常都不是，表现为"没有出现附件条"，看起来
就像还在上传。现在都走 `composer-error` 那一行，下一次尝试开始时清掉——留在成功消息旁边
的陈旧报错本身就是一句假话。

录音上传失败是其中最糟的一种：录音**没法重打**。

## 2026-08-14 — 打开的房间自己标记已读

每次消息变化都把水位线推到屏幕上最新的一条（[[unread.ts]] 的 `markTeamRead`）。
只在挂载时标记一次的话，用户坐在那里读的正是把行标记出来的那些消息，而行还标着。

**这里的规则和服务端相反，且是有意的**：服务端决定「这值不值得一个标记」（排除
用户自己的消息和平台通知），面板记录「用户看到了什么」——渲染在他面前的一行，
不管是谁写的，都已经被看到了。标记少于屏幕上显示的内容，会让一个只是自己念叨了
两句的房间永远挂着标记，而用户没有任何办法清掉它。

两个 surface 推同一个水位线（另一个是 [[AgentList]]，它只看得到列表响应）。两边
都是单调的，所以谁更靠前谁生效，谁也无法把对方推回去。
## 2026-08-10 — 巡查行渲染

`msg_type === 'patrol'` 走独立分支:虚线框 + 「Leader 巡查」小标 + Markdown 正文。

**不能当普通气泡渲染**,两个原因:巡查是以房间自己的标记(`team_<id>`)落墙的,
`author_name` 会解析成裸 id;而且画成气泡会让 Leader 看起来在不停打断房间,
而它其实是平台在盘点。

## 2026-08-07 — 停止留痕渲染成系统行

`msg_type === 'system_stop'` 的消息走独立分支:居中的小胶囊,文案
`chat.team.stoppedNotice`(带 agent 名)。**不复用气泡**——把它画成 agent
自己的回复,读起来就像 agent 在宣告自己的死亡;这条消息是房间在说话。

团队任务是当众跑的,所以也应当众停止:没有留痕,其他成员只看到一个凭空
消失的任务,只能猜是跑完了、崩了、还是还在跑。

## 2026-08-07 — 右侧挂上团队工作台

根布局从纵向 flex 改为「横向 flex：transcript 列 + [[TeamWorkspacePanel.tsx]]」。此前该组件
注释里的「artifacts 暂不提供」不再成立。

## 2026-08-11 — composer 底色对齐单聊(card 白 + hairline)

团队 composer 此前落在 ui/Textarea 默认的 paper-warm 填充上,与单聊
Composer.tsx 的 card 白底不一致——Owner 双截图对照抓出,并由此在
design_system.md 补了 §2.5 表面层级("同一功能件跨页面同层"/"输入面与
容器差一层")。现补上与单聊相同的 `bg-[--nm-card]` 覆盖;发送键 rest 态
warm 填充在白底输入框上可见,与单聊完全一致,零改动。


## 2026-07-31 — roster v2 接线：accent 下传 + drawer 不再定宽

TeamRosterPanel 拿到 `accent`（团队色）画选中态；移动端 drawer 的
className 去掉 w-64 —— 宽度归 roster 自己（256↔430px 呼吸），定宽会
把展开变宽顶掉（cn 是 tailwind-merge，后写的宽度赢）。

## 2026-07-31 — 每条 agent 回复挂自己的 reasoning 展开条

气泡内 `BusAttachmentList` 之后渲染 [[TeamMessageProcess]]（仅
`!is_user && m.event_id`）——单聊 MessageBubble "View reasoning & tools"
的团队版。数据链：trigger 发帖时把 turn 的 event_id 写进
`bus_messages.event_id` → chat GET 序列化 → 气泡按需 `getEventLog`。
历史消息 event_id 为 null，自然不显示按钮（无死按钮）。分工：**消息气泡
负责历史（每条 turn 各自可开）**，roster 详情只负责 live/最近一轮。

## 2026-07-30 — guide 横幅退役：空房 hero + member bar 的 `?` popover

`TeamRoomGuide` 那条常驻灰字横幅整个删掉（文件已删），寻址规则改由
[[TeamRoomHero]] 提供的两个出口承载：空房时 `messages.length === 0` 分支渲染
hero（替掉原来的 `Users2 + chat.team.empty` 块，`chat.team.empty|emptyHint|
emptyHintWithLead` 三个 key 就此在代码里无人读），有消息之后靠 member bar 里
`?` 按钮弹出的 `GuideRuleCards` popover。横幅是「提示债」——为一个用户只需要
知道两次的事实永久占掉一条房间宽度，还得靠 localStorage 记折叠状态才勉强能忍。
新方案两处都是按需出现，`nx.team.guide.*` 记忆随之消失，无需迁移。

`?` 容器接管了原先 Settings2 身上的 `ml-auto`（Settings2 去掉，否则两个
`ml-auto` 会把设置钮推到自己一组里）。popover 靠 `guideRef` + document 上的
`mousedown` 监听外点关闭，且只在 `guideOpen` 为真时挂监听——常挂会让每次点击
都过一遍这段判断。

## 2026-07-30 — 两栏布局：roster 常驻，console/气泡退役

Timeline+Composer 外包一层两栏容器，右侧挂常驻 [[TeamRosterPanel]]（桌面
恒显，窄屏由 member bar 的 Users2 钮开 overlay drawer）。据此退役两个旧
表面：顶部折叠 console（TeamActivityConsole 文件已删）和消息流底部的
TeamActivityBubble——后者的 idle-trace 保留期正是「跑完后气泡赖着不走」
的根源。消息流只剩本文件内的 TypingIndicator：仅 running 成员渲染、无
统计、回复落地即消失；点击它展开 roster 里该成员的详情（expandedId 状态
由本组件持有，两侧共享同一高亮，这就是 roster 用受控 props 的原因）。


## 2026-07-28 — moved into `chat/team/`, activity + guidance split out

The surface reached three files, so it became a package (铁律 #23):
[[TeamActivityConsole]] (status console + transcript bubble) and
`TeamRoomGuide` (addressing help, retired 2026-07-30) moved out of this file; the panel now
composes them. `@/components/chat/TeamChatPanel` → `@/components/chat/team`.

What the panel itself kept:

- **One clock.** A single `now` (1s ticker, epoch ms) is passed to every child
  so no two durations on screen disagree by a tick. The ticker now runs while
  ANY member is non-idle, not only `running` — the `queued` "waiting Nm" and
  `stalled` "silent Nm" counters are the whole point of those states.
- **`lead_agent_id`** from the response drives both the guide's "who answers"
  line and a dot on that member's avatar in the roster.
- `thinking` is gone; the empty state and the bubble list derive from
  `activity` (see [[teams]]).

The old inline status strip and the dumb "…" queued bubble are replaced by the
console and [[TeamActivityConsole]]'s bubble respectively.


## 2026-07-21 — voice input (mic), parity with single-agent chat

Tools row gained an `AudioRecorder` (mic) next to the attach `+`. Records → uploads with
`source:'recording'` → backend Whisper → the memo joins `pending` and renders as a
`VoiceTranscript` chip; on send it flows like any bus attachment (agents get the transcript
in their marker). Reuses the ChatPanel plumbing: a mount `getTranscriptionAvailability`
probe, a click-time `onPreflight` re-check, a "voice unavailable" `<Dialog>` (→ Settings),
and a post-record notice banner. New i18n keys `chat.team.transcriptionUnavailable|
voiceUnavailableTitle|voiceUnavailableBody|voiceUnavailableProbeFailed|openSettings|cancel`.

## 2026-07-21 — user can attach files in the composer

Composer gained a paperclip button + hidden multi `<input type=file>`. Picked files upload
immediately via `api.uploadTeamChatAttachment` into a `pending: BusAttachment[]` state,
shown as removable chips above the textarea; `handleSend` passes `pending` to
`api.sendTeamChat` and clears it (restores on failure). An attachment-only message (no text)
is allowed. New i18n keys `chat.team.attach|uploading|removeAttachment` (en+zh).

## 2026-07-20 — bus attachments render in the room

Each message bubble now renders `<BusAttachmentList attachments={m.attachments} />`
below the text, so files an agent sent/shared into the team room show as
chips/thumbnails. `TeamChatMessage` gained `attachments?: BusAttachment[]`
(populated by `GET /api/teams/{id}/chat/messages`). See [[BusAttachmentList]].

# chat/team/TeamChatPanel.tsx — Team group-chat surface

## Why it exists

The user-facing view of the homepage's "agent team": one team's shared room,
rendered in the SAME main slot as the single-agent [[ChatPanel]] so switching
between an agent and a team is seamless (see [[MainLayout]]'s `TeamChatView`).

## How it works

Messages flow over the **message bus**, NOT a single-agent narrative:
- **Send** → `api.sendTeamChat(teamId, content, mentions)` → `POST
  /api/teams/{id}/chat/messages`. The composer text's `@tokens` are resolved to
  member `agent_id`s (or the literal `"@all"`); the backend posts as the
  synthetic sender `usr_<user_id>` and maps `@all` → bus `"@everyone"`.
- **Transcript** → polls `GET /api/teams/{id}/chat/messages` every `POLL_MS`
  (3s). The response also carries `thinking: string[]` — members the bus trigger
  is currently processing — which drives the "…" typing bubbles.

## Design decisions / gotchas

- **@-mention autocomplete** with an `@all` option pinned on top; keyboard
  (↑↓/Enter/Tab/Esc) + click. `@all` is a `MentionOption` kind, not a member.
- Bubbles mirror the single-agent [[MessageBubble]]: carbon-soft (user, right,
  carbon right-edge) vs silicon-soft (agent, left, silicon left-edge), meta row
  outside the bubble. Agent content is rendered through `<Markdown>` (the replies
  are markdown); user content stays plain text. `.content.trim()` + the global
  `.markdown-content > :first-child/:last-child { margin: 0 }` rule keep the
  agent bubble's vertical padding equal to the user bubble's.
- The room itself is created/owned by the backend (`created_by = team_<id>`);
  this panel never touches the bus directly — it only calls the two team-chat
  routes. Agent replies (and agent→agent @ cascades) are produced server-side by
  the MessageBusTrigger and just appear in the polled transcript.

## 2026-07-22 — team activity visualization

Consumes the new `activity` from `getTeamChat` ([[teams]]). Renders a top **status strip**
(chip per running/queued member: dot + name + phase·elapsed) and, at the bottom of the
timeline, an **activity bubble** per active member — running shows a spinner + live phase
(思考中 / 调用 <tool> / 回复中) + elapsed; queued shows the "…" dots. A 1s ticker advances
elapsed between the 3s polls. Replaces the old dumb `thinking` "…" bubbles. i18n
`chat.team.activity.*`.

## 2026-08-10 — the workspace loader has a second trigger

It keys on `messages.length`, on the reasoning that a turn which registered
something has just landed in the transcript — cheap and honest for the case it
was written for. It misses exactly one: a wipe of the team's FILES, which
empties the panel while leaving the transcript unchanged, so the panel went on
listing rows the server had deleted and every one of them 410'd on click.
`workspaceRefreshTick` from [[chatStore.ts]] covers it.

## 2026-08-11 — 公告栏：状态、入口、系统消息

公告栏的数据和重载持有在这里（和工作台同一个理由：变更会在 transcript 里落系统行，
两个面必须对「什么时候变了」有共识）。每次写入后**同时**重载公告栏和 transcript，
而不是等下一个轮询周期才让 transcript 追上。

入口放在房间标题栏，是**常驻 chrome，不藏在设置里**——公告栏回答的是
「这个团队已经知道什么」，那是边读房间边问的问题。按钮带条目数，
让已存在的公告栏**自己宣告存在**，而不是等着被发现。

`system_bulletin` 照 `system_stop` 的先例渲染成居中灰行：公告栏变更是**房间在说话**，
不是某个成员在说话；套成成员消息就把平台事件归给了恰好触发它的那个人。

面板加载 effect 也挂在 `workspaceRefreshTick` 上：清团队数据可能带走公告栏，
面板留在屏上列已删掉的规则比空着更糟。

## 2026-08-13 — 投递通知渲染

`system_undelivered` / `system_delivery_failed` 两个新平台行，照 `system_stop` /
`system_bulletin` 的先例渲染成居中胶囊行——平台在说话，不是成员在说话。

两种行**视觉权重不同**：上墙失败是**我们的错、可追责**，用 warning 色；静默一轮只是
信息，和别的平台行一样安静。失败原因不进正文，落在 `title` 上（hover 才见）——
transcript 保持安静，排障的人一个 hover 就能拿到原因。

`content` 是给纯文本消费者的英文兜底，**不是**给读者看的：数据库不知道读者的语言，
所以正文照 `stoppedNotice` 的路子走 i18n key。测试里专门钉了「英文兜底句子不上屏」。

对应后端：[[delivery_notice]]、[[message_bus_trigger]]。

## 2026-08-12 — 消息渲染抽件（1134 → 970 行）

抽出 [[TeamMessageBubble]] / [[TeamTranscript]] / [[TeamSystemLine]] / [[TeamMessageFooter]]。
**只抽本批要动的**——整体重排作为独立 PR 更好 review，混进行为改动里更难。

被抽走的三块（系统行、footer、时间戳）markup 与理由**原样搬运**，本批不改它们的行为。

## 2026-08-13 — 增量轮询与滚动礼让

轮询改为携带 `since`（见 [[mergeTeamMessages]]）。**首次加载仍然整体替换**：
切换房间时往陈旧数组里合并，会把上一个团队的消息显示在新团队名下。

transcript 用 `messagesRef` 而不是把 `messages` 放进 `refresh` 的依赖：
依赖变化会**每来一条消息就重建一次定时器**，那正是把 3 秒轮询变成快得多的轮询的方式。

滚动只在读者已经贴底时跟随（见 [[scrollStickiness]]）。
