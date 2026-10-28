---
code_file: frontend/src/components/chat/team/TeamWorkBoard.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — patrol 开关搬去团队管理 tab

`togglePatrol`/`patrol-toggle` 删除,`api.setTeamPatrol` 不再从这里调;「上次巡查/已关闭」
的痕迹文字保留;轮询到的 `patrol_enabled` 写进 teamsStore(`notePatrol`),痕迹文字也**从 store 读**
(`patrolByTeam[teamId] ?? true`),本地不再持有副本;管理 tab 翻开关这里立刻跟着变。
空板时组件返回 null 但轮询 effect 仍在喂 store。空板子一律不渲染(此前 patrol OFF 时保留面板只是为了留住开关)。
开关见 [[TeamManagePanel.tsx]]。测试:`TeamWorkBoard.test.tsx` 「patrol switch moved out」。


# TeamWorkBoard.tsx — 团队欠着什么

## 与 roster 的分工

roster 回答「每个成员**此刻**在干什么」;工作板回答那个**活得比一轮更久**的
问题:「我们说好要做什么,有没有卡住的」。在板子存在之前,任务只活在讨论它的
那一轮里,所以屏幕上没有任何东西能显示一条流程正在无声死去。

放在 roster 列**下方**而不是混进成员行里 —— 两个问题不同,合在一起会让「谁忙」
和「欠什么」互相淹没。挂在 `TeamRosterPanel` 内部,桌面与窄屏 drawer 两个渲染
点因此自动共享。

## 两个状态是用户的事

- **`stalled`** 由平台从真实活动数据推导,所以可以当**事实**展示,而不是猜测。
- **`paused`** 是停止一棵 run 树留下的。刻意**可见**(被停的任务不能看起来像被
  删了),而且**只有用户能恢复** —— 巡查故意不碰它,否则一次扫描就把 owner 的
  停止撤销了。

## 设计决策

- **空板子什么都不渲染**,不是渲染一个空状态标题。为一个不存在的东西留常驻
  chrome,正是这个房间一路在还的债(横幅、折叠 console)。
- **巡查痕迹只在这里显示**。健康的巡查在房间里一言不发(设计如此),所以这是
  用户唯一能看到「它确实在跑」的地方。
- `_ago` 不碰 i18n,只算数;所有译文归调用方。否则 helper 会悄悄变成第二个
  文案来源。
- 恢复后**重新拉取**而不是本地改状态:服务端可能把它退回 `open`(无人认领)
  或 `in_progress`(还有 assignee),前端不该猜这个分支。

测试:`__tests__/TeamWorkBoard.test.tsx`

## 2026-08-10 — 「空板不渲染」有了一个例外,例外才是要记的部分

上面「**空板子什么都不渲染**」现在是有条件的:

```tsx
if (items.length === 0 && patrolEnabled) return null;
```

巡查被**关掉**时,空板子**仍然渲染**。因为巡查开关是用户设的常驻状态,而这个
面板是把它开回来的唯一入口 —— 无条件藏掉面板等于把开关也藏了,用户没有任何
路径撤销自己上一次的选择。

这条例外必须写在这里:只看到「空板不渲染」的人会顺手把 `&& patrolEnabled` 删掉
当作冗余条件,然后开关就再也打不开了。

**巡查开关本身**(顶部的 toggle + 上次巡查时间)是拍板项 #3「可关」的唯一 UI
落点。走 `PUT /teams/{id}/patrol`,面板先乐观翻转、失败时立刻翻回(其后 5s 轮询
再兜一层)—— 开关是用户意图,不该等一次往返才有反馈。

## 2026-08-21 — 卡片有了两种 `kind`,交接卡不是任务卡

板子过去把每行 work_item 都当同一种卡渲染(`title` + `assignee_name · status`)。
问题:一条 @ 了多个 agent 的消息会被 `message_bus/errand.py` 扇出成**每人一行**
的 `origin=auto` 交接单,每行复用**发件人首行**当 title。逐行渲染就把同一句话在
板上出现一次每个收件人,还挂到**没说这话的人**名下(实锤见 dev team_a50745c97d15,
一条 msg → 两张同文卡)。

现在后端(`get_work_board` 的 `_assemble_work_board`)把同一 `source_message_id`
的 auto 行**合并成一张交接卡**,视图带 `kind`:

- `kind==='task'`(即 `origin=tool` 的显式任务):渲染不变 —— `title` + 单个
  `assignee_name · status`。
- `kind==='handoff'`(合并后的 auto 交接):渲染 `source_name → assignee_names`,
  下行是 `awaitingReply · status`。**故意不显示消息正文** —— 那是发件人的话,
  钉在收件人名下会被读成收件人说的。i18n 新增 `awaitingReply`、`nameSep`(名字
  之间的分隔符,zh/ja 用「、」、ar 用「، 」、其余「, 」)。

**恢复(resume)对交接卡是「只恢复 paused 子集」**:一张交接卡背后是多行,但可能
只有一部分被 paused。`parked` 由 `status==='paused' || paused_item_ids 非空` 决定
(handoff 聚合成 `in_progress` 时仍可能有 parked 行);resume 只对 `paused_item_ids`
发请求,用 `Promise.allSettled` 而非串行 `for await` —— 一行失败不能把其余行搁在
半恢复态。失败的行下一轮轮询会重新出现在 `paused_item_ids` 里,按钮因此不消失。
task 卡的 `paused_item_ids`/`item_ids` 也覆盖到,所以一条 `resume(item)` 同时服务
两种卡。改这里时别退回「按 status 单值判 parked」或「串行 for await」—— 那会让
被停的交接卡在部分失败后永久失去恢复入口。

这条改动**只动看板显示**:巡查/stalled 走 `list_active`、errand 开关单走
`list_open_errands`,都不经过 `list_visible`,不受影响。
