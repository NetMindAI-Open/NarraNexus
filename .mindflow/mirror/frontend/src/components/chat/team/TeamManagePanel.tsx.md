---
code_file: frontend/src/components/chat/team/TeamManagePanel.tsx
last_verified: 2026-09-03
stub: false
---

# TeamManagePanel — 团队房间的「团队管理」抽屉 tab

## 2026-09-03 — 新建

把散在四处的管理控件收成一个 tab,自上而下:①公告栏(复用 [[TeamBulletinPanel.tsx]],
state 归 [[TeamChatPanel.tsx]]);②组长 select(回调 `onSetLead`,与花名册卡片同一条
`updateTeam` 链);③patrol 开关(从 [[TeamWorkBoard.tsx]] 搬来:**只读 teamsStore 的 `patrolByTeam[teamId]`**,
由房间 3s 轮询与看板轮询写入,写走 store `setPatrol`(乐观+失败回滚);未上报或写入飞行中(`patrolInFlight`)按钮禁用,settle 窗口内可点;
文案用 `chat.team.manage.patrolOn/patrolOff`);④成员增删(账号下全部 agent,
走 teamsStore `addMember/removeMember`);⑤「编辑资料」= 内联 [[../../teams/TeamProfileForm]](名称/颜色/简介,走 store `updateTeam`);
**不挂** TeamManagementModal——它左栏可切任意团队、可建团队、其删除不会带房间离开(I9)。
组长/成员/删除各只有本面板一个编辑入口;⑥清理数据(`ClearTeamDataDialog`
→ `api.clearTeamData` → `onCleared(scopes)` 让房间丢掉对应内容);⑦删除团队(confirm 后
`deleteTeam`,`navigate('/app/chat')`)。

store 一律用 **selector 形式**(`useTeamsStore((s) => s.addMember)`):房间的测试把
store mock 成 selector hook,弹窗那种解构写法在那里会炸。
被拿掉的旧入口:工具条公告栏按钮/齿轮、看板 patrol 开关、左侧团队行菜单「清理数据」。
守卫:`__tests__/TeamManagePanel.test.tsx`。
