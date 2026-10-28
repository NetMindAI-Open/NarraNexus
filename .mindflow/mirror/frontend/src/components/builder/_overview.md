---
code_file: frontend/src/components/builder/
last_verified: 2026-09-03
stub: false
---

# components/builder/ — Agent 创建工作室

## 这个目录做什么

「通过 AI 创建」这条路径上新增的 UI：

| 文件 | 职责 |
|---|---|
| [[ProviderPickerModal.tsx]] | provider 准入闸门（设计里的方案 B 弹窗） |
| [[BuilderConfigPanel.tsx]] | 右侧配置面板 —— 对话把它填好 |
| [[providerRows.ts]] | `/api/providers` 的松类型 map → 弹窗要画的行 |

## 2026-09-03 — 从「markdown 草稿」改成「结构化面板 + 实时落库」

初版是：agent 用既有的 `register_artifact` 写一份 `agent-config.md` 进 artifact
列，用户点一个按钮整体写进 Awareness。Owner 实机看过后指出，右侧要的是**原始
设计里那个结构化面板**（名称 / 指令 / tool 推荐），不是一份文档。

于是 `ApplyDraftBar.tsx` 与 `lib/builderPrompt.ts` 删除，
[[ArtifactColumn.tsx]] 还原，改为本目录的 [[BuilderConfigPanel.tsx]] +
`<agent_draft>` 协议（[[builderProtocol.ts]]）。

**关键认识**：结构化面板其实很便宜。v1 设计之所以贵，贵在 carrier + 草稿态
agent 那套双 agent 生命周期，**不在面板本身**。既然改成「直接用当前 agent」，
agent 已经是真的、已经选中了，面板直接绑上去实时写即可 —— 纯前端，零后端。

## 面板挂在哪

不是新开一列：右侧抽屉本来就是「一个 tab 一个 panel」，所以加了一个
`builder` 原子 tab（[[tabs.ts]] / [[BookmarkPanelHost.tsx]]），
[[ChooseCreateMethodPage.tsx]] 建完 agent 后 `requestPanel('builder')` 亮出它。

## 目录边界

对话本身不在这里 —— 它就是普通聊天页。线格式在 [[builderProtocol.ts]]，写入在
[[builderApply.ts]]，与聊天回合的接缝在 [[useStudioTurn.ts]]，开关与推荐在
[[builderSession.ts]]。
