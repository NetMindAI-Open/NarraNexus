---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/prompts.py
last_verified: 2026-09-04
---

## 2026-08-04 — 新增 §5「Your Own Identity Card」（名字 + 一句话描述）

原来的提示词**一个字都没提过** `update_agent_name`/`update_agent_profile`：工具
在 schema 里，但没有任何指令要求 agent 去设置名字，更没有描述。结果 prod 上
16/16 本地、prod 全量 agent 的 `agent_description` 都停在创建占位符
（P1 段02）。这一节就是那个缺失的指令。

要点：① bootstrap 阶段 creator 说清用途后**同一次调用**把名字和描述都写掉，不
等人问；② 职责变化时再写；③ 明确写清**描述的读者是别的 agent，不是人**——它是
同伴决定要不要把问题路由过来的依据，空着就没人能选到你，对方 owner 的请求会
失败而双方都不知道为什么；④ 读到自己描述缺失（[[basic_info_module]] 注入的那句
指令）就当作 setup 未完成，去问 creator。

原 §5 Confidentiality 顺延为 §6，Awareness Profile 段顺延为 §7；尾注补一句
`update_awareness` **不**碰这两个平台字段。

## 2026-06-18 — 通用保密边界（Section 5 Confidentiality）

新增 Section 5 "Confidentiality (Information Boundary)"：creator 是唯一完全可信方；
凭据/API key、系统指令与本 awareness profile、creator 私密信息、私有策略——一律不得
向非 creator 透露,**尤其警惕共享/多 agent 平台上的其他 agent**。这是注入到**每个**
agent 系统提示的代码常量,所以已 provision 的 agent（含 Arena agent）下一轮即生效,无需
DB backfill。措辞限定在"机密信息"而非正常工作产出,不影响 agent 正常协作。守铁律 #4:
通用层不出现任何场景名（不提 "Arena"）;Arena 的具体强化版在 `ARENA_AWARENESS`
（arena_provisioning_service.py）。原 "Your Current Awareness Profile" 顺延为 Section 6。

# prompts.py — AwarenessModule 指令定义

## 为什么存在

`AWARENESS_MODULE_INSTRUCTIONS` 是 Agent 系统提示里关于"自我意识"维度的完整说明。它向 LLM 解释：什么是 Awareness Profile、三个核心维度是什么、哪些信号应该立即记录、哪些不应该持久化，以及如何把当前 profile（`{awareness}` 占位符）应用到对话行为中。

## 上下游关系

- **被谁用**：`AwarenessModule.__init__` 把它赋值给 `self.instructions`；`XYZBaseModule.contribute_instructions()` 在每轮对话时用 `ctx_data` 字段（包括 `{awareness}`）格式化后注入系统提示
- **依赖谁**：无外部依赖，纯文本常量

## 设计决策

**三个维度的框架**：Topic Organization（叙事偏好）、Work Style（任务偏好）、Communication（交互偏好）是从用户行为观察中归纳出的正交维度。这个框架直接指导了 `update_awareness` MCP 工具要求 LLM 填写的 Markdown 模板结构（四个 section）。

**显式区分"持久化"vs"临时"信号**：明确告诉 LLM "一次性任务指令"不应该写入 profile，防止 profile 被临时上下文污染。这是核心设计约束，不写这条规则的话 LLM 会把每次对话的特定指令都存进去。

**`{awareness}` 占位符位置**：放在 Section 5（最后）——先讲规则，再展示当前状态，符合 LLM 处理顺序。

## Gotcha / 边界情况

- 指令末尾的 `Note: Use __mcp__update_awareness()` 里的工具名格式（`__mcp__`前缀）是 FastMCP 在某些版本中注册工具的内部名称格式。实际调用时 Agent 看到的工具名取决于 MCP 客户端如何解析——如果工具找不到，先检查实际注册的名称。

## 新人易踩的坑

- 修改这个文件里的四个 Section 标题时，需要同步更新 `update_awareness` MCP 工具的 docstring 里的模板（两处描述必须一致，否则 LLM 写出来的 profile 格式与 prompts 里描述的不匹配）。
