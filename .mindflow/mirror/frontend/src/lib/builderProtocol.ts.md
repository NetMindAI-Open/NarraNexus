---
code_file: frontend/src/lib/builderProtocol.ts
last_verified: 2026-09-04
stub: false
---

## 2026-09-04 — `takeText` 先 trim 再截断，截断不留孤立高位代理

入库值就是侧边栏显示的值（原来判空用 trim、返回却是原串）；第 255 位正好切在 emoji 中间时
削掉尾部孤立高位代理。测试里 `not.toContain('note')` 那条易碎断言改为解析信封后断言
`available_skills` 对象。

## 2026-09-03 (评审修订) — 空串回落、长度截断、目录「未知」、锚定剥离

PR #382 评审的 🔴#1 / 🟡#4 / 🟡#8 与两条 Minor 都落在这个文件：

- **空串 = 「本轮不碰这个字段」**，不是「清空」。INSTRUCTION 亲手把全空骨架
  贴给模型当样例，弱模型某一轮照抄骨架是铁律 #15 要求容忍的常态；照抄一次曾等于
  三个写请求把名称 / 描述 / 认知一起抹掉，且面板没有旧值备份、不可撤销。
  `takeText` 对空白串也回落。三个字段**分别**判定 —— 只填 awareness、name 留空
  是完全正常的一轮。列表字段不动：`skill_ids: []` 仍是「显式移除」。
- **name / description 按 `AGENT_TEXT_MAX_LENGTH` 截断**（取自 [[agentLimits.ts]]，
  后端常量镜像）。选截断不选回落：超长通常意味着模型确实想改这个字段只是啰嗦。
  截断在 merge 里做而不是写入时做，否则 `next` 与落库值不一致，下一轮 diff 又判
  「变了」、每轮重发同一个 422。awareness 是长文本列，不套这个上限。
- **目录 `null` = 未知，区别于 `[]` = 已知为空**。未知时 `skill_ids` 整体回落到当
  前推荐，不过滤、不存盘 —— 否则 marketplace 抖一次就把之前几轮已通过校验的推荐
  永久清空。已知为空时照旧拒掉所有 id（目录是真相）。
- **信封瘦身 + 截断告知**：`SkillOption` 只剩 id + name（每轮重述，description ×
  60 条是用户按消息付费的几 KB）；目录被 `CATALOGUE_LIMIT` 截断时信封写明
  「first N of M」，目录未知时写明不可用 —— 不留静默上限。
- **`decodeBuilderTurn` 锚定到开头**：信封只会是消息前缀，锚定后用户原句里出现
  标记字面量不再被吃掉。

## 已知取舍（评审 🟡#9，本轮不做，记录在此）

Builder 指令 + 配置信封随 `outgoing` 一起进了 chatStore 和后端事件 / Narrative，
`decodeBuilderTurn` 只在渲染时剥离。studio 跑 N 轮，agent 的长期记忆里就有 N 份
「You are acting as the NarraNexus Agent Builder」。可预期后果：studio 关闭后 agent
仍可能以配置助手口吻回话、尾部吐 `<agent_draft>`（被 strip 静默吃掉）。
两个方向：(1) 存储 / 发送用户原句、信封作为一次性前置片段随 `run()` 走不入
Narrative 的字段 —— 跨层改 `run()` 签名与后端持久化，不塞进本 PR；(2) 接受污染，
但 studio 关闭后仍收到 draft 块时给用户明确提示而非静默吞掉（只能提示，铁律 #15
不许拦截或改写）。**倾向 (1)，单独立项。**

# builderProtocol.ts — 创建工作室的收发线格式

## 出站：为什么每轮都重述配置

studio 打开期间，用户发的**每一条**消息都带上 Builder 指令 + 目标 agent 的
**当前配置**。重述不是冗余：

- 它让用户在面板里的手改**成为权威** —— 模型看到的是面板真实持有的值，于是
  「修订」而不是凭记忆「覆盖」。
- 只在第 1 轮说一次的话，弱模型几过几轮就不再吐 `<agent_draft>` 块了。铁律
  #15 规定平台不干预用户的模型选择，所以必须容忍一个会忘事的模型。

## 入站：三条容错，都不是假设

这三条是实际会发生的，按咬人频率排：

1. **流式期间只有开标签**，闭标签要过很多帧才到。所以 strip **必须两条正则**，
   只匹配闭合形态的话，整段原始 JSON 每轮都会在读者眼前滚过去。
2. **CLI 类模型在 JSON 字符串里吐真换行**（值是 Markdown 时尤其）。第二次
   parse 只转义**字符串字面量内部**的控制字符 —— 对象结构仍须是合法 JSON，
   这是容忍一个已知习惯，不是写 JSON 修复器。
3. **不认识的一律丢弃**：目录里没有的 skill id、白名单外的 channel、缺失字段。
   fail-closed；parse 失败降级为「本轮不改配置」，绝不打断对话。

## 关键决策

- `parseAgentDraft` 取**最后**一个块，不是第一个 —— 重述过的块，末尾那个最新。
- 合并时**显式空数组是被尊重的**（那是移除东西的方式），但缺失字段回退到当前
  值（模型漏一个字段不该把用户的配置清空）。
- `channels` 只表达**意图**。绑定渠道需要凭证，凭证从用户直达后端，绝不进这个
  信封。

## 上游 / 下游

[[useStudioTurn.ts]] 是唯一调用方（encode 出站、parse+merge 入站）；
[[builderApply.ts]] 负责把合并结果写成真实改动；渲染侧剥离在
[[MessageBubble.tsx]] 与 [[SegmentedReply.tsx]]。
