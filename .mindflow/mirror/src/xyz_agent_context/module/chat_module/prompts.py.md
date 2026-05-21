---
code_file: src/xyz_agent_context/module/chat_module/prompts.py
last_verified: 2026-05-21
---

## 2026-05-21 — 复杂信息优先 HTML artifact（含可见性边界）

Guidelines 段新增一条："复杂 / 结构化 / 长的 owner-facing 回复 → 优先做成
HTML artifact + 一句 pointer message"，而不是把一大段文本塞进 chat。理由：
artifact 在 owner 的 web/桌面 chat UI 里渲染，远比纯文本清晰。**关键边界**：
artifact tab 是 chat UI 的一部分，所以只对 `chat` turn 有意义——IM 通道
（lark / slack / telegram）的发送者**看不到** artifact，给他们的回复必须把
内容放进 channel message 本身。artifact 工具由 always-on 的 common-tools 能力
提供，how-to 在 [[common_tools_module.py]]（本 prompt 只给指引、不重复 how-to，
保持 module 解耦）。配套：common_tools 的 `#### Visual Artifacts` 段同步强化了
"复杂信息默认 HTML" + 同一条可见性边界。

## 2026-05-19 — warm-with-humans 范围扩到所有 IM 通道

最初实现只覆盖 `working_source=chat`，但 Lark / Slack / Telegram 通道
的实际对方也是真人——按 [[hook_schema.py]] 的 `is_from_human()`
分类，这四类都该走 warm 风格。新版 Guidelines 把规则的适用范围明确写到
**`chat / lark / slack / telegram`**，并在 Rule of Thumb 段把
`job / message_bus / callback / skill_study` 归入"agent / system partner —
brevity beats politeness"。配套：[[message_bus_module.py]] 的 Reply
Discipline 段在 MESSAGE_BUS 触发时强制 brevity + `[NO_REPLY]` 纪律。

# prompts.py — ChatModule 指令定义

## 为什么存在

`CHAT_MODULE_INSTRUCTIONS` 的核心使命是建立 Agent 的"思考 vs 说话"认知模型，
明确**"说话"唯一通道是 MCP 工具 `mcp__chat_module__send_message_to_user_directly`**，
并按 `working_source` 分层规定"应该说话"和"酌情说话"。这是整个系统里最关键的
prompt 约束之一——如果 Agent 没有正确理解"不调用这个 MCP 工具就等于没说话"，
用户将永远看不到 Agent 的回复，UI 上只会出现 `(Agent decided no response needed)`。

## 上下游关系

- **被谁用**：`ChatModule.__init__` 把它赋给 `self.instructions`；每轮对话时注入系统提示
- **依赖谁**：无外部依赖，纯文本常量
- **对齐对象**：
  - `xyz_agent_context/schema/hook_schema.py::WorkingSource` 枚举
    （prompt 里的 trigger 分层表格必须与枚举值一致）
  - `frontend/src/stores/chatStore.ts:228-247` 的 fallback 逻辑
    （prompt 里写的失败 UI 字符串 `(Agent decided no response needed)`
    必须与 chatStore 里硬编码的字符串一致——否则教学失去 grounding）

## 设计决策

**显式写出 MCP tool 全名**：2026-04-22 之前 prompt 只用裸名 `send_message_to_user_directly`，
但 Claude Agent SDK 对 MCP 工具的注册名是 `mcp__<server>__<tool>` 格式，
真实名字是 `mcp__chat_module__send_message_to_user_directly`。裸名在 ToolSearch /
deferred tool loading 机制下会命中不到，LLM 会在 thinking 里抱怨 "tool not
available" 然后放弃说话。现在 prompt 开头明确给全名，并在 anti-patterns 段
专门反模式化 "搜裸名得到空结果就放弃"。

**按 `working_source` 分层定义说话纪律**：旧版 prompt 把 "用户直接对话 / background
job / IM 频道" 混在一个 Scenarios 表格里且用的是口语化分类，LLM 很难精确映射到
实际的 `working_source` 值。新版把 7 个枚举值（`chat` / `job` / `lark` /
`message_bus` / `a2a` / `callback` / `skill_study`）——每一个都列出来：
`chat` 是 ⭐ STRONGLY EXPECTED，其余都是 ⚖️ Agent 自行判断。这是"区别对待用户主动会话
vs 后台自动触发"的核心。

**`chat` 从 REQUIRED 软化为 STRONGLY EXPECTED**（2026-04-27 调整）：
旧版强硬要求 `chat` turn 必须以一次 send_message 调用结束，"never silent"。
但现实场景里 owner 偶尔会发 "ok" / "thanks" 这种不期望回复的消息，硬性要求 agent
回复反而产生噪声。新策略：**默认仍然要回复**（"almost every turn ends with one call"），
但允许 agent 基于 owner 显然不期待回复的情况主动选择静默。关键在于让 agent 把
"不回复" 当作**自觉决定**，而不是**侥幸遗漏**——区分这两者是 Pre-Completion
Self-Check 的设计目的。

**Pre-Completion Self-Check 段（2026-04-27 新增）**：放在整个 instructions 的最后，
利用 LLM 对 prompt 末尾的高 recency attention。两段式自检：
- Q1（决定要不要说）：列了 5 种典型场景，告诉 agent 怎么判断；明确说 silence on
  chat is "deliberate exception, not default"，避免 agent 把"决定不回复"当借口逃避
- Q2（如果要说，是否用了对的通道）：直接命名 UI 失败字符串
  `(Agent decided no response needed)`，把抽象的"通道选错"钉到 grounded specific
  failure mode 上。LLM 对具体可见的失败模式比抽象警告反应更强
- 末段「silence is fine — but make sure that's a decision, not an oversight」
  闭环重申选择权，同时用 oversight 一词反向施压

这是为了修 Bug T2 (channel-misroute silent turn) 加的：the operator 在 2026-04-27 报告
EVE agent 在工具链任务（Bash / Read / Write / Glob / lark_status）后，明明
thinking 里说要 "report back to the user"，最终却用 inline text 收尾，没调
send_message_to_user_directly，导致 UI 显示 `(Agent decided no response needed)`。
分析显示 agent **有**通道意识（其他 run 的 thinking 显式提到该 tool），但工具链
长 context 后注意力衰减导致遗忘。Self-Check 段在 prompt 末尾给出最后一次 nudge。

**Owner-vs-Sender 区分专门成段**（2026-04-27 强化）：增加了一个独立小节明确说明
`send_message_to_user_directly` **永远是发给 owner**，无论 working_source 是什么。
旧 prompt 只在 trigger source 表格里隐含说"reply on Lark via lark tools"，
但没有正面说"this tool always goes to the owner, never to the channel sender"。
现在新增的"Channel routing"表格逐 source 说明这个 tool 做什么，避免 agent 把
"reply to the Lark sender" 错误地翻译成 send_message_to_user_directly 调用——
那会导致 owner 被 channel chatter spam，同时 channel 上的真正发送者一无所知。

**用表格和类比强化核心概念**："你的文本输出用户看不到"是反直觉的（大多数 LLM
训练数据里 final_output 就是用户可见回复）。Prompt 用了声音隔离室类比和明确的
对比表格来强化这个概念。这些重复强调是必要的，不是冗余。

**Anti-Patterns 列表**：明确列出错误做法（转发每条 IM 消息、发送进度更新、
重复确认收到任务、把 `working_source=chat` 静默结束、搜裸名放弃）比只说
"什么时候发"更有效。负面示例对 LLM 行为约束效果更强。第一条
anti-pattern 在 2026-04-27 调整：从"never end chat turn silently"软化为
"don't end with intended-for-owner content as inline text"——精确针对真实失败模式
（intent 存在但通道走错），而不是统一禁止 silence。

## Gotcha / 边界情况

- **`(Agent decided no response needed)` 字符串硬编码**：prompt 里引用了这个
  UI 字符串作为 grounded failure mode，前端 `chatStore.ts:243` 也硬编码了同样
  的字符串。改前端 fallback 文案时必须同步更新本 prompt。
- **`working_source=chat` 的 final-answer 约束**是为了防止 Agent 做了大量工具
  调用和研究，但最后忘记调用 MCP 工具总结结果。这个约束和"不要发没必要的消息"
  之间存在张力——已通过 Pre-Completion Self-Check Q1 的 5 种场景列表给出判断
  框架，agent 不再需要凭直觉权衡。
- **MCP 工具名前缀的可变性**：如果未来 `ChatModule.get_config().name` 改了，
  MCP server name 会跟着变，所有 `mcp__chat_module__*` 前缀就会失效。改 module
  名称必须同步更新本 prompt 里的 6 处全名引用。
- **后端兜底是 `endswith` 匹配**：`chat_module.py:256` 用
  `tool_name.endswith("send_message_to_user_directly")` 检测，所以 prompt 教
  LLM 用全名调用不会破坏后端识别——兼容。
- **"User" 与 "Owner" 用词**：2026-04-27 之后逐步把 prompt 里的"user"改为"owner"
  以消除"用户" 的歧义——在 lark/message_bus 等场景下，触发 turn 的"sender"和
  agent 的"owner（创建者）"不是同一个人，混用 user 这个词会让 agent 困惑。
  目前还有少量历史"user"措辞保留在表格之外（如 trigger source 表格里的"the
  user"），未来一致性整理时可全面替换。

## 新人易踩的坑

- 给 Agent 添加新的触发场景（`WorkingSource` 枚举加值）时，必须在这个 prompt
  的"By Trigger Source"表格 + "Channel routing" 表格 **两处**都加一行明确
  说明新场景的发送规则，否则 Agent 会 fallback 到"不知道该不该发"而随机发送。
- 想"简化"prompt 把所有 `mcp__chat_module__send_message_to_user_directly`
  改回裸名 `send_message_to_user_directly`——会回到 2026-04-22 之前的"find
  tool fails → silent turn"bug。保留全名是刚性要求。
- 想删掉 Pre-Completion Self-Check 段以"减少 prompt 长度"——这段是 prompt 末尾
  专门用来对抗注意力衰减的，删了会让 Bug T2 复发（EVE 工具链任务后忘调 tool）。
  Self-Check 的位置（最后一段）和 grounded UI 字符串都是经过设计的，不要随意改。
- 想把 chat 行的 ⭐ STRONGLY EXPECTED 改回 ✅ REQUIRED——会让 agent 在 owner
  发"ok"/"thanks" 时也强行回复，产生噪声。新版的 nuance（"几乎都要回复，但
  允许有意识地选择静默"）是经过权衡的。
