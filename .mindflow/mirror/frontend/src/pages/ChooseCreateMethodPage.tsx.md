---
code_file: frontend/src/pages/ChooseCreateMethodPage.tsx
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — AI 路径改为开 studio + 亮出面板

建完 agent 后 `openStudio(agentId)`（[[../stores/studioStore.ts]]）并 `openPanel('builder')`。面板必须在第一
条消息之前就可见 —— 这条路径的卖点就是「对话把面板填好」，面板不在场就看不出
发生了什么。

# ChooseCreateMethodPage.tsx — 创建路径的分叉页

## 为什么存在（产品理由，不是技术理由）

创建 agent 原本是一次点击直达聊天页，实际观察到的结果是**用户没意识到自己创建
了什么** —— 心智停在「session + 解决任务」。归因是路径**过于轻松**，思维从 0
直接跳到 100，缺少循序渐进建立认知的过程。这一页就是那个刻意的停顿。

所以「这一页多了一次点击」不是代价，**它就是功能本身**。评估时要看走完全程的
用户后续留存，而不是这一步的漏斗损耗。

## 两条路径最终是同一个调用

两张卡片都落到既有的 `useCreateAgent()`，建出来的空白 agent 完全一样。
AI 路径只多两件事：

1. 先过 [[ProviderPickerModal.tsx]] 确认有 provider；
2. 在 [[../stores/studioStore.ts]] 上打开这个 agent 的 studio，让它的**每一条**出站
   消息携带 Builder 指令，直到用户离开 studio。

## 关键决策

**这一页自己不创建任何东西**，创建发生在下一步。所以退出去零副作用 —— 这也是
v0 没有「草稿 agent」需要清理的原因。

**provider 探测 fail closed**：探测报错时打开弹窗，而不是放行。误拦一次只花
一次点击；误放行的代价是一段死掉的对话，用户还得自己诊断。

## 上游 / 下游

- 入口：[[Sidebar.tsx]] 的 CreateMenu「+」，以及 [[AgentList.tsx]] 零 agent
  空态里的 CTA（那正是这个分叉服务的首次使用者）。
- 团队内建 agent 的路径**没有**改道 —— 那个流程已经有明确目的地和理由。
- 出口：`/app/chat`。
