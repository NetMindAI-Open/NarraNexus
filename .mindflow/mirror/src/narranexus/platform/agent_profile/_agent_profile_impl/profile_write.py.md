---
code_file: src/narranexus/platform/agent_profile/_agent_profile_impl/profile_write.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — 改名后刷新首程问候

`renamed_from` 分支记完身份更正后调用 `refresh_bootstrap_greeting_after_rename`，占位名时渲染的「我还没有名字」问候随之换成带名字的版本。

## 2026-08-19 (八改) — 签名即白名单；两个信号都成三态

**`extra_updates` 换成具名参数** `is_public` / `created_by`。今天不可利用（两处
调用点都在代码里手工拼 dict），但下一个人写
`extra_updates=body.model_dump(exclude_none=True)` 是很自然的一步，那一刻「改个
名字」就顺带能改 agent 的**归属人**——而这个函数叫 profile change，审查的人不会
想到去查鉴权。[[entity_schema]] 的闭集判据是第二道闸，不是第一道。
`is_public=None` 表示"没传"，`False` 是值。

**`identity_note_recorded` 也成了三态**（上一轮只改了 `identity_reconciled`，
留下的不对称被第八轮抓到）：`None` = 没有可写入的地方（agent 没有 AwarenessModule
实例，也就没有会变旧的身份记忆），`True` = 写了，`False` = 该写没写成。

为什么这条不是洁癖：`POST /manyfold/agents` 建号**从不调 `InstanceFactory`**
（对比 [[provision]] 会调 `create_agent_level_instances`），所以每个 Manyfold
provision 出来、还没跑过第一轮的 agent 改名时都会落到"无实例"这支。报成
`False` 的后果是：UI 弹「它可能仍然自述旧名」、Manyfold 收到 false、日志打
WARNING——**三句全是假的**，那个 agent 根本没有身份记录。会误报的告警等于没有
告警，而这个字段存在的唯一理由就是事故状态不能只活在会被轮转掉的日志里。

**auth 路由的兜底分支**不再把 `result.error` 直接丢给 UI：那串是给**模型**写的
（"Error: new_name is too long (max 255 characters)" 在弹窗里像泄漏的内部字符串），
两个受众任何一边改措辞就会漂。按 `error_kind` 映射到本路由自己的句子。

## 2026-08-19 (五改) — 三处修正

1. **延迟导入的隔离效果被我说大了**。实测 `from narranexus.platform.module_system import
   awareness_module` 会连带拉进 **22 个兄弟模块包**（Python 必然先导入父包，
   父包 `__init__` 建 module_registry）。延迟买到的是**归属**——本包与其上的路由不再
   持有对 Module 层的模块作用域依赖——**不是** import 期隔离。docstring 已改准。
   这是本次第三次把断言写得比事实强。
2. **`not_applied` 那一支现在也刷名录**。dev 上的承诺是「每个被接受的请求都刷」，
   而且刷在回读校验**之前**；并发覆盖恰恰是名录可能过期的场景，原来是唯一会跳过
   修复的路径（#320 的论证）。
3. **`name_clash_with` 不再被两条 HTTP 路径丢掉**。事务一直在算它，agent 自己的
   工具一直在报它，而界面改名把它扔了——于是「把一个名字转给第二个 agent」在
   UI 上是**静默**发生的，那正是 P1 段02 ① 的起点。`UpdateAgentResponse` 加了
   `name_clash_with`（附加字段，老客户端忽略），manyfold 的响应体同样带上。
   **不拦**，只是不再静默。

已知未修（记在 `reference/self_notebook/todo/2026-08-19-awareness-profile-read-modify-write-race.md`）：
profile 的读—改—写现在跨进程可达，`merge_identity_change_note` 那句「绝不丢内容」
的承诺覆盖不到整段并发覆盖。

# profile_write.py — 改名事务

> 本文件由 [[_awareness_writes]] 搬来（2026-08-18）。搬迁理由见
> [[_overview]]；下面是事务本身的设计，历史条目在 `_awareness_writes.py.md`。

## 事务包含什么（缺一件就退回事故）

1. 写 `agents` 行（name / description / `extra_updates`），**一次**行写
2. **身份更正**写进 Awareness profile —— 延迟导入（买到的是归属，不是隔离；**无容错分支**，缺包即抛——十五改删掉了只在四分之一调用点成立的 ImportError 守卫）
3. 刷 `bus_agent_registry`

## 三个反复咬人的区分

**「是不是改名」≠「要不要写」**。存储值未归一化（`"  old  "`）时，归一化后与目标
相等——**不是改名**，但**必须写**，否则那行永远改不了名（谓词比较归一化值，永远
判"已相等"）。`is_rename` 与 `_stored_text_is_unnormalized` 因此是两个判断，
`renamed_from` / `renamed_to` 只跟前者走。

**「写了什么」≠「什么没落地」**。`updated_fields` 与 `unapplied_fields` 分开。合成
一个字段时，含义会随 `ok` 翻转，而下一个做写入埋点的人不会先判 `ok`——**这种错
永不抛异常**，只让指标悄悄偏高。同一类错在这个 PR 里犯了两次（`updated_fields`、
`renamed_to`），第二次是审查抓的。

**「这次调用改没改名」≠「记录对不对」**。这是最贵的一条：笔记原来只在**本次写了
列**时才写，于是**所有已经变异的 agent 都无法修复**——工单里那个 prod agent 行是
「小绿」、记录断言「美食家」，用户把名字改成「小绿」走 no-op 分支，被告知成功而什么
都没变。#320 的形状，低一层。现在义务归**状态**不归写入：记录与行不一致就纠正，
无论这次写没写。`identity_reconciled` 与 `identity_note_recorded` 分开报，
`renamed_*` 保持"这次调用改没改名"的含义。

## 短路是地基，不是优化

`update_agent` 返回 `cursor.rowcount`：SQLite 数 matched、MySQL 数 changed。等值
短路（含 `extra_updates`，第一版漏了、被既有测试当场抓到）保证"没变化就不发写"，
下游才可以**完全不解释那个数字**——落没落地一律以**回读**为准。

## 给后来者

**别再新增 `agents.agent_name` 的写入方**。要写，调本包。护栏命令与 8 行允许清单
在 [[_awareness_writes]]。已知缺口（bundle 导入不刷名录、且原样搬运身份记录）记在
`reference/self_notebook/todo/2026-08-18-bundle-import-identity-gap.md`。

## 2026-09-04 · services + host hooks (batch 3c.6)

`_record_identity` / `_reconcile_identity` fire `onDidChangeAgentName` / `onDidSettleAgentName` (`utils/host_hooks`) with the transaction's db; the `_awareness_identity_writers` import seam is gone. Listener exceptions are re-raised so the transaction's existing log-and-continue handling is unchanged; no listener (awareness disabled) → None, matching the no-instance answer.
