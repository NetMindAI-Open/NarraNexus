---
code_file: src/xyz_agent_context/context_runtime/context_runtime.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 (review 修正) — team 房中央门控：整轮空 expressive

review Critical #1：只在 bus module 挡 team 房不够——ChatModule 无条件
声明（IM 渠道也会声明），team 轮 expressive 非空 → 两框架的 reminder 在
最贴生成点的位置说「纯文本不送达」，与 team prompt 的「纯文本自动上墙」
对撞。收口移到收集处：`bus_team_room` 标记为真时整轮声明为空（天然命中
claude 适配器 append_reply_reminder 的 no-op 分支与 NexusPower 空契约）。
同批修正：收集点上方的契约注释（原文还写着被推翻的 "must be
priority-driven"）与 3 元组类型标注（实际已是 4 元组）。

## 2026-08-04 — expressive 收集改 origin-first 排序

排序键从 (priority, module_class) 变为 (origin_rank, priority, module_class)：
拥有本轮 working_source 的模块（`owns_working_source(ws)`，见 [[base]]）
origin_rank=0 排最前。第一个收集到的工具即框架的默认回复工具
（NexusPower constitution 的 example + claude 适配器 reminder 首位），
从此跟着「谁联系的你」走，而不是恒为 priority 1 的 owner-chat 工具——
bus 轮默认 bus_send_message、wechat 轮默认 wechat_send。钩子调用
fail-open（无此方法/抛错 → rank 1，纯 priority 序不变），假模块与
旧路径零影响。

## 2026-08-03 — 注入本轮的**差事作用域**(而不是升级 turn_source)

header 除 turn_source 外再带 `bus_errand_peer` / `bus_errand_channel`
(来源:[[message_bus_trigger]] 分类器判定,经 trigger_extra_data →
`ctx_data.extra_data`);turn_source **保持** working_source 原值。

曾经的错做法(同一 PR 内自我推翻):MESSAGE_BUS + 差事延续时把 turn_source
整轮升级成 `BUS_ERRAND_TURN_SOURCE`。整轮盖章会波及**同一轮里发给其他同伴的
回答**——bus 未读是跨 channel 注入、且模块提示词要求回答的,于是那个同伴把
回答读成提问、不再向自己 owner 回报,P1 换个座位复发(2026-08-03 review
round 4)。所以这里只注入**事实**(我这轮的差事跟谁、在哪个 channel),把
「这一条算不算差事提问」交给知道目标的 send 现场判断(见
[[_message_bus_mcp_tools]] 的 `_send_turn_source`)。

作用域在模块循环外算一次,同轮所有 server 拿同一份;显式
`X-NarraNexus-Errand-*` header 与 bearer 位置字段双通道(codex 只转发
bearer——字段数约定见 [[_mcp_identity]])。

## 2026-08-04 — 盖章处补 user_id（W1，回合属主上同一 seam）

`agent_id_headers(...)` 增传 `user_id=self.user_id`——回合属主与 agent
身份走同一注入点、同一双通道（header + bearer 第 5 位）。`self.user_id`
可能为 None（无属主的触发回合），builder 对 None 省略 header、bearer 尾
字段掉落，服务端回退用模型参数。服务端的弱纪律（占位符才注入、None 不
碰、mismatch 只计量）见 [[_mcp_identity]] 2026-08-04 条。

## 2026-08-04 — 声明收集点 TypeError 单列(review)

fail-open 只该兜"某个模块自己坏了";覆写签名漂移是全站接线 bug,
改为 logger.error 且注明 declaration DROPPED(教训 #3:别把报警吞成
背景噪音——ChatModule 曾因此静默哑掉)。
## 2026-08-03 — `get_expressive_tools` 增加可选 ctx_data(按来源声明)

回复面声明可按 turn 来源变化——声明面绝不能列出本回合无法投递的死工具
(那是喂给模型的错误信息,弱模型遇声明/指令冲突时常以"写成文字"收场)。
首个消费者:narramessenger 托管回合剔除 trigger 捕获式的 narra_reply,
只声明 narra_send。无 ctx 调用方(测试/旧路径)行为不变。

## 2026-08-01 — mcp_servers spec 注入调用者身份 header

`mcp_servers[name] = {"url": …}` 变成同时带 `headers=agent_id_headers(self.agent_id)`。
这是 P1「Agent 消极回复"我做不了"」的注入侧:模块 MCP Server 由所有 agent
共享,此前工具的 `agent_id` 完全由模型填,填了 `agent_current` 就硬失败。
这一行是**唯一**注入点(两个适配器消费同一个 spec dict);读取与解析在
[[_mcp_identity]]。选 header 不选 URL query 是实测结论——query 在 SSE
传输上会丢。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`build_input_for_framework` 在 MCP 收集环里同批收集各模块
`get_expressive_tools()`(fail-open,姿态同 disallowed),收集后按
**(priority, module_class) 全序排**(R4d 同源,与 _build_turn_context_block /
_sorted_module_instructions 完全一致)再去重——**首位即默认回复工具**且会被
冻进框架稳定前缀,所以顺序必须由优先级驱动、跨回合确定。**不能**依赖
active_instances 原序:那是 created_at DESC 的公共实例序(review 2026-07-31
抓出的 bug:后建的 channel 实例会抢走首位,把 lark_cli 写进 constitution
默认例子,还让新建实例静默打穿全量 cache)。返回值 3 元组 → 4 元组;
`ContextRuntimeOutput.expressive_tools` 承载。

## 2026-07-29 (二次) — 原生 turn 回放(NexusPower)

`build_input_for_framework` 新增 `_load_native_turn_replays`:框架为
NATIVE_REPLAY_FRAMEWORKS(现=nexus_power,同 step_3 的 identity 解析同源)时,当前
narrative 的 assistant 行展开为 [[history_projection]] 从 events.event_log 折回的
assistant/tool 消息序列(独白+工具调用+配对结果),替代两行拍平摘要。user 行保持拍平
(时间线 tag 锚定);跨 narrative 行(`memory_type=short_term`)与无可折 log 的行保持
拍平;窗口外仍由 narrative summary(system prompt Part 2)覆盖——「近期逐字、中期摘要、
远期检索」。逐层 fail-open:回放是增强,任何失败退回拍平行,绝不炸 turn。
`pop()` 防重复行二次注入同一工具序列。

## 2026-07-29 — `build_input_for_framework` 不再拼 timeline 阅读指南

`enhanced_system_prompt` 去掉了 `+ CHAT_HISTORY_TIMELINE_PREAMBLE`；该常量已移到
[[materializer.py]]，与 history 区块同生共死。这里拼的话，argv 预算不够把 30 行
全驱逐后，system prompt 里仍留着一段"下面是你们最近的对话"，模型会去回忆一个不
存在的时间线（prod 2026-07-29）。注意本文件 `run()` 里打的
`System Prompt built: N characters` 是在 preamble / recent-actions 拼接**之前**
的数字——排查提示词尺寸时别把它当最终值。

## 2026-07-28 — R4d：module 块全序排序 + 诊断改按发射序并加前缀分桶哈希

**(1) module 块排序此前不是全序（等长重排型缓存断点）**

`_build_module_instructions_prompt` 原来是 `sorted(key=lambda x: x.priority)`。
`sorted` 是稳定排序，**优先级相同的模块只能继承上游顺序**，而上游是
[[instance_repository.py]] `get_public_instances()`——它此前**没有 order_by**
（同类的 get_by_agent / get_by_agent_and_user / get_chat_instances_by_user 全都有）。
现网真实存在的同优先级并列：BasicInfo(2)/GeneralMemory(2)、
Awareness(3)/SocialNetwork(3)、Lark(6)/Discord(6)/Slack(6)、
Telegram(7)/WeChat(7)。Awareness↔SocialNetwork 一次对调会搬动约 4018 与 4880
字节而**总长度不变**——这是「等长重排」，缓存前缀在第一个换位块处断裂，而所有
按字节数的诊断都报告"没变化"。SQLite 今天恰好返回 rowid 序所以本机没发作；
Postgres/MySQL 不承诺任何顺序（堆表重读、执行计划切换、index-only scan 都会重排），
**这是 cloud 上的定时炸弹，不是理论问题**。

修法（两层都修）：

- 新增 `_sorted_module_instructions()` 静态方法，key = `(priority, name)`，
  成为 module 块顺序的**唯一权威**：发射路径
  （`_build_module_instructions_prompt`）与两个诊断
  （`_log_system_prompt_breakdown` / `_maybe_dump_system_prompt`）全部走它，
  所以**日志打印的顺序 = prompt 拼接的顺序**。`name` 是模块类名，上游已按
  module_class 去重，故 `(priority, name)` 是真全序。
- `_build_turn_context_block` 的 module 块排序同步改成
  `(priority, module_class)` 全序（该块进的是 message 不是前缀，本身不是缓存
  断点，改它是为了"module 块顺序"在全代码库只有一种含义）。元组从
  `(priority, block)` 变成 `(priority, module_class, block)`。
- 数据层：见 [[instance_repository.py]] R4d 条目（`get_public_instances`
  补 `order_by="created_at DESC"`）。

**(2) `[SYSPROMPT-BREAKDOWN]` 此前会主动隐藏等长重排**

`modules:` 段原来按 instruction 长度**降序**打印——两轮之间发生块重排时，
这个列表打印结果完全相同，诊断成了共犯。改为按**发射序**打印
（`_sorted_module_instructions`），重排就表现为 token 换位。

同时新增 `_prefix_bucket_hashes()` + `_PREFIX_BUCKETS`（2000/8000/32000 字符），
在同一行尾部追加 `pfx2k=<6hex> pfx8k=<6hex> pfx32k=<6hex>`：对被测字符串前 N 字符
取 sha256 截 6 位。用途是**定位**——整串的 `ctx_sha256` 只能回答"变了没"，
分桶能回答"变在哪一段"：pfx2k 不同 → 断在前 2K（narrative 元数据区）；
pfx2k 相同而 pfx8k 不同 → 断在 2K–8K（module 块边界区）；以此类推。
不再需要抓包或 dump 才能定位一次等长分歧。

兼容性：`_log_system_prompt_breakdown` 新增**可选** kwarg `prompt_text`
（不传则不输出 pfx 字段，行尾仍以 `ctx_sha256=` 结束），
`total=` / `parts:` / `narrative:` / `modules:` / `ctx_sha256=` 字段与
`[SYSPROMPT-BREAKDOWN]` 前缀全部保持原样，现有日志工具与测试照旧可解析。
调用点在 `build_input_for_framework` 里传 `enhanced_system_prompt`。

**(3) Part 1 注释同步**：narrative 稳定半的字段清单去掉 created_at
（见 [[prompts.py]] R4d：created_at 有两个时钟源，已迁 turn 块）。

测试：`tests/context_runtime/test_module_block_order.py`（随机 20 次洗牌 →
拼接结果恒等；Awareness↔SocialNetwork 对调是等长且无害；turn 块反序输入 →
结果相同；仓储层 order_by 断言）、
`tests/context_runtime/test_system_prompt_breakdown.py`（发射序、三个分桶哈希、
分桶定位一次等长替换、无 prompt_text 时行尾形状不变、dump header 同为发射序）。

## 2026-07-28 — R4c：MCP server 字典确定性排序 + 哈希仪器改标 ctx_sha256

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

- `build_input_for_framework` 收集完 mcp_servers 后 `dict(sorted(...))` 按
  server 名排序（E2 §4：tools 数组跨轮洗牌是第二道缓存断点；本层字典顺序此前
  跟随 active_instances 迭代序）。逐 server 内部工具序 = FastMCP 注册序（代码
  序，确定）；**CLI 侧跨 server 并发连接的合并序仍不可控**，见
  [[adapters/claude/sdk.py]]。codex 两条路径本就 sorted，此改动补齐 claude 路。
  注意 `pass_mcp_servers` 在 StepContext 层 merge（本方法之后），最终排序由
  claude 适配器 `_build_claude_mcp_config` 的 sorted 兜底。
- **仪器校准（E2 §6.3）**：本文件 [SYSPROMPT-BREAKDOWN] 行的哈希改标
  `ctx_sha256=`——它哈希的是 ContextRuntime 层字符串，**不含** claude 适配器
  冷启动轮追加的 `=== Chat History ===` 尾段与逐 system-message 拼接换行，
  不能代表 system[2] 实发字节。权威 `sys_sha256=` 现由 claude 适配器
  post-`assemble_argv_prompt` 发射（[SYSPROMPT-SHA] 行），`grep sys_sha256`
  只会命中真实发送字节的哈希。

## 2026-07-28 — R4a turn-context relocation（system prompt 字节稳定，token 优化三期）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

**目的**：Anthropic/DeepSeek 前缀缓存是字节/块级的，system prompt 里任何每轮易变
字节（Part 0 temporal 秒级时间戳、Part 1 narrative 的 updated_at/current_summary、
尾部 recent_actions）都会打穿缓存。R4a 把这些**非模块**易变段整体搬到**当前轮
user message 前部**的 `[Turn context]` 块（照 2026-07-09 附件 marker 先例：只改
LLM-facing 的 current_user_content，**绝不动 `ctx_data.input_content`** → 聊天持久
化/前端/冷启动历史重合成零感知）。只搬不删（铁律 #16）：模型每轮照样看到全部内容。

- 开关 `settings.prompt_turn_context_relocation_enabled`（默认 true；env
  `PROMPT_TURN_CONTEXT_RELOCATION_ENABLED`）。**关 = 装配与 R4 之前逐字节一致**
  （temporal/narrative 完整模板/recent_actions 全部回原位）——fail-open 运维闸门。
- `build_complete_system_prompt`：开关开时跳过 Part 0、Part 1 用稳定版模板
  （`combine_main_narrative_prompt(include_volatile=False)`，见
  [[prompt_builder.py]] 模板拆分）。
- 新增 `_build_turn_context_block(active_instances, ctx_data, narrative_list)`：
  固定顺序 temporal（块名 "User Temporal Context" 不变，job MCP docstring 引用它）
  → narrative turn 块 → 模块 `get_turn_context` 块（module_class 去重、priority
  升序稳定排序，与 `_build_module_instructions_prompt` 同语义）→ recent_actions。
  逐 part fail-open（warning + 跳过，不打死轮次）。R4a 阶段无模块 override
  （R4b 才逐模块搬），模块块为空。
  **所有 part 都为空时返回 `""` 而非只剩 header**，调用点同样跳过包裹 ——
  否则会给用户原话前缀两个空行加一个"分隔了个寂寞"的 `--- User message ---`，
  并让模型去找一个不存在的小节。这条路径只在 temporal 失败（唯一常驻 part）
  且无 narrative / 无模块块 / 无 recent_actions 时可达。
- `build_input_for_framework` 新增 kw 参数 `narrative_list`（run() 传入；None =
  无 narrative turn 块）；`[Turn context]` + `--- User message ---` separator 前置
  拼接在附件 marker 逻辑**之前**。
- **[SYSPROMPT-BREAKDOWN] 发射点从 build_complete_system_prompt 移到
  build_input_for_framework**（哈希"最终送适配器的 system prompt 字符串"，度量什么
  就哈希什么）：行尾追加 `sys_sha256=<sha256(enhanced_system_prompt)[:12]>`，
  parts 增加 `turn_context=<chars>`；`total=` 语义变为 enhanced（含 preamble）长度。
  breakdown 输入经 `self._last_part_sizes / _last_module_instructions /
  _last_narrative_meta` 在两方法间传递（ContextRuntime 每轮一个实例，不跨轮泄漏）。
  两轮 grep sys_sha256 相同 = 前缀稳定哨兵（R4b 收尾后才翻转为相同——Anthropic
  all-or-nothing，R4a 单独合入时 BasicInfo 等模块仍易变属预期）。
- history 与 system prompt 的关系更新：历史仍走 unified timeline role messages
  （不变）；**当前轮消息现在 = [Turn context] 块 + separator + 用户原话 (+ 附件
  marker)**，claude/codex 适配器 `messages.pop()` 原样取走，零适配器改动（铁律 #9）。

Plan：`reference/self_notebook/plans/2026-07-25-r4-prompt-stability.plan.md`（R4a）。
Tests：`tests/context_runtime/test_turn_context_relocation.py`（开关关闭时恢复
pre-R4 **小节位置** / 装配顺序 / 模块收集 fail-open / sys_sha256 稳定性 /
空 turn context 不发 header）、`test_temporal_context.py`（建造点迁移）。

**用词更正（2026-07-29，PR #185 review）**：早期注释与文档写的"开关关闭 =
与 pre-R4 字节相同"是夸大。关闭开关恢复的是**小节位置**，不是字节流 ——
三条 determinism normalisation（narrative 时间戳规范化、模块块 (priority, name)
全序、mcp_servers 排序）是无条件生效的，它们不搬运也不丢弃任何内容，但确实改字节。
module 级的"这段模板没动"仍然是准确的窄声明，只有 assembly 级的说法被改掉。

## 2026-07-24 — `build_input_for_framework` 新增第三返回值 `disallowed_tools`（B++）

返回值新增第三项：汇总各模块 `get_disallowed_tools()`（见 [[base.py]] 通用面 /
[[channel_module_base]] channel 覆写）的全限定工具名列表，排序去重。单模块收集
失败 **fail-open**（warning + 跳过——宁可多花 token 也不误伤已绑定 channel）。
用途：未绑定 channel 的工具 schema 不进模型上下文，经 [[context_schema.py]]
`ContextRuntimeOutput.disallowed_tools` → [[step_3_agent_loop.py]] → driver
kwargs 下传。Plan：token 优化 W2 B++。

## 2026-07-15 — `build_input_for_framework` 返回 MCP spec dict

返回值第二项从 `{name: url}` 改为 `{name: {"url": url}}`（模块内部 MCP 无
headers）；用户外部 MCP 的 headers 由 backend 装配层（websocket/skills）注入
`pass_mcp_servers`。命名统一 `mcp_servers`。

## 2026-07-14 — [SYSPROMPT-BREAKDOWN] 诊断日志（system-prompt-growth 事故取证）

`build_complete_system_prompt` 现在在 return 前打一条 INFO
`[SYSPROMPT-BREAKDOWN] agent=… total=… | parts: security/temporal/narrative/modules/bootstrap=各字节 | narrative: nar_summary_chars/nar_dynamic_entries | top_modules: 最大 5 个模块指令`。
纯诊断、不改行为。**动机**：观测到 system prompt 逐轮增长(app ~100k、dev ~115k 上限
`MAX_SYSTEM_PROMPT_LENGTH`),逼近上限后历史被驱逐、agent(含原生 opus)停止调
`send_message_to_user_directly`。此前每 Part 字节只在 `logger.debug`(生产 INFO 级看不到)。
新增纯静态 helper `_log_system_prompt_breakdown`(可单测,见
`tests/context_runtime/test_system_prompt_breakdown.py`)。narrative 的 `current_summary`
字节 + `dynamic_summary` 条数是增长头号嫌疑,单独打出来量化。

## 2026-07-10 — 移除写死的假模型身份（改由 BasicInfoModule 动态填）

`run()` 构造 `ContextData` 时曾写死 `agent_info_model_type="Claude Agent SDK"` +
`model_name="sonnet-4"`，经 basic_info [[prompts.py]] 的 "LLM Model" 段灌进系统
prompt → **每个** agent（含 codex_cli+gpt5）都自称 Claude Sonnet-4，被问模型就照读
（违反铁律#9）。两行 kwargs 已删；这两个字段改由 [[basic_info_module.py]]
`hook_data_gathering` 经 [[providers/model_identity.py]] 按真实 slot 动态填。
ContextRuntime 从此不掺和模型身份（本就不该知道），字段也在 [[context_schema.py]]
正式声明了。

## 2026-07-09 — current-turn attachment marker injection

`build_input_for_framework` 追加"当前 turn user message"时，读 `ctx_data.extra_data["attachments"]`，通过 `Attachment.markers_from_dicts(agent_id=ctx_data.agent_id, user_id=ctx_data.user_id)` 合成 marker 拼在 LLM 视图的 content 尾部。**关键：不动 `ctx_data.input_content`**——那个字符串会被 `ChatModule.hook_persist_turn` 原样写成用户消息的 `content`，`backend/routes/agents/chat_history.py` 又会把它回显到前端。marker 只走 LLM 视图，绝对路径不进 UI 也不进 DB。

`ctx_data.user_id` 已被 `AgentRuntime` 覆写为 agent owner（`_agent.created_by`，agent_runtime.py:245），marker 里的路径拿到的就是 owner workspace 的绝对路径——跟 trigger 落盘时的路径一致，agent Read 直接命中。

覆盖范围：所有 IM 渠道 + WS 前端 chat 都已把 `attachments` 放进 `trigger_extra_data`（channel_trigger_base.py 的 `_build_and_run_agent` line 1175-1178；backend/routes/websocket.py line 679），零 trigger 侧改动。下一轮读历史时 `ChatModule._synthesize_attachment_markers`（同一 `Attachment.markers_from_dicts` 底层）再合成一次，marker 格式两条路径完全一致，agent 行为在当前 turn vs 历史 turn 上无差别。

Regression：`tests/channel/test_current_turn_attachment_marker.py` 5 条锁死"注入不动 input_content / owner routing / malformed 有 WARNING / 空列表零动作"。

## 2026-06-17 — system prompt 第一段注入安全铁律(**云端专属**)

`build_complete_system_prompt` 在所有其它段(temporal / narrative /
module / bootstrap)之前 append `prompts.SECURITY_IRON_RULES`,确保没有后续
段落或用户消息能覆盖它。**仅当 `get_deployment_mode()=="cloud"` 时注入** ——
铁律是多租户保护;本地/桌面是用户自己的机器,用户就是要 agent 跨自己的文件夹
干活,注入它会废掉本地体验,且本地没有别的租户/平台密钥要保护。详见
`prompts.py.md`。

## 2026-06-12 — User Identity Context block REMOVED (治本: moved into basic_info)

The `_build_user_identity_block` method, its "Part 0b: User Identity" injection
in `build_complete_system_prompt`, and the `USER_IDENTITY_CONTEXT` import are
all gone. That block was a redundant second place to inject owner/sender
identity — the canonical identity injection lives in [[basic_info_module.py]]
(`hook_data_gathering` + basic_info `prompts.py`), which is where the human-name
fix now lives. Removing it avoids two competing identity sources in the system
prompt. See the 2026-06-11 entry below for what the now-deleted block did.

## 2026-06-11 — User Identity Context block (owner + sender, by human name)

build_complete_system_prompt now injects a "Part 0b: User Identity" block via new `_build_user_identity_block(ctx_data)`: states the agent OWNER by display_name (NetMind nickname / local display_name; falls back to user_id, never shown as a name otherwise), and — when the trigger carries `sender_user_id` in extra_data (only chat does) — whether the current sender is the owner or a visitor (resolves their display_name, compares to owner). IM triggers don't set sender_user_id (their own module trust block handles sender), so they get only the owner line; job/bus likewise. Cleanly separates user_id (opaque scoping key) from the human name. Defensive: lookup failure never breaks the prompt.


## 2026-05-29 — EverMemOS removed

The "Relevant Memory" prompt section is gone. `build_complete_system_prompt`
and `run()` no longer take `relevant_episodes`; `_build_relevant_memory_prompt`
was deleted; `_build_auxiliary_narratives_prompt` no longer takes
`evermemos_memories` (it now renders only the auxiliary narrative summaries).
System prompt is now: temporal context → main narrative → module instructions
→ bootstrap. Long-term memory is the current narrative's full history, surfaced
by [[chat_module.py]] as the unified timeline (see note below).

## 2026-05-20 (Fix #2 P1) — render the unified timeline; drop the cross-narrative system-prompt section

`build_input_for_framework` no longer splits chat_history into long/short and
no longer injects cross-narrative memory as a separate system-prompt section
(via `_build_short_term_memory_prompt` + `SHORT_TERM_MEMORY_HEADER` — now
DEPRECATED/unused). It renders the single unified timeline (built by
[[chat_module.py]]) as real role messages, each prefixed by
`_format_timeline_tag()` → `[time · topic · nar_id]` plus the channel source
prefix, and prepends `CHAT_HISTORY_TIMELINE_PREAMBLE` to the system prompt to
teach the agent how to read it (tags, how it was assembled, and what the user
can/can't see — reasoning is private). `[CHAT-CTX] unified timeline rendered`
log line reports total / cross / current counts. `_format_timeline_tag` now also
emits `evt=<event_id>` per line (for view_event drill-down).

## 2026-05-20 (Fix #2 P2) — recent-actions section in the system prompt

`_build_recent_actions_section` renders `ctx_data.extra_data['recent_actions']`
(populated by [[chat_module.py]] `_load_recent_actions`) as a compact
`RECENT_ACTIONS_HEADER` block appended to the system prompt — one line per
background activity `- [time] <source>: <job title / summary> (evt=<id>)`. Kept
separate from the conversation timeline so background work doesn't pollute it.

## 2026-05-19 — `_source` carried on final_messages

`build_input_for_framework()` now stamps each long-term history row with
an internal `_source` field copied from its `meta_data.working_source`
(default `"chat"`). Consumed by [[adapters/claude/sdk.py]] for
source-aware truncation: when the system prompt + history would exceed
the SDK's argv ceiling, oldest background-trigger rows
(`job / message_bus / lark / callback`) are evicted first; chat rows
are kept until the budget can't be met any other way. Other SDK
adapters (OpenAI Agents, Gemini) build their own message dicts so this
extra key never reaches them.

# context_runtime.py — the assembly engine that turns raw Narrative + Module state into a ready-to-submit LLM payload

## 为什么存在

Before each LLM call, the agent needs a fully formed system prompt and a message list. That assembly is non-trivial: it requires pulling the right Narrative summary, firing every active module's data-gathering hook, sorting module instructions by priority, routing conversation history into two memory tracks (long-term vs. short-term), truncating oversized messages, and collecting MCP server URLs for tool access — all in a deterministic order. `ContextRuntime` owns that entire assembly pipeline so the orchestration layer (`step_3_agent_loop.py`) can hand it a Narrative list and an instance list and receive back a `ContextRuntimeOutput` without knowing anything about how the prompt was built.

Without this class, the assembly logic would bleed into `AgentRuntime` steps, each module would need to know about every other module's output format, and the prompt structure would become impossible to reason about or test in isolation.

## 上下游关系

**Receives from:**
- `step_3_agent_loop.py` (inside `agent_runtime/_agent_runtime_steps/`) is the exclusive runtime caller. It constructs a `ContextRuntime` instance with the `agent_id`, `user_id`, and a `DatabaseClient`, then calls `.run()` with the Narrative list and active module instances produced by earlier pipeline steps.
- `NarrativeService` (`narrative/`) — called inside `build_complete_system_prompt()` to format the main Narrative's summary prompt via `combine_main_narrative_prompt()`.
- `HookManager` (`module/hook_manager.py`) — invoked in `run()` Step 1-2 to fire `hook_data_gathering` on every loaded module, which allows modules like `ChatModule` to populate `ctx_data.chat_history`.
- `AgentRepository` (`repository/`) — queried directly inside the Bootstrap injection block to look up who created the agent, bypassing `BasicInfoModule` to avoid a module-load dependency. **Bootstrap deletion is now profile-driven (2026-06-16)**: the auto-delete threshold is no longer a hard-coded `>= 3` — it comes from `bootstrap.profiles.auto_delete_threshold_from_meta(agent_record.agent_metadata)` (missing key → historical default 3; `None` → never rule-delete, semantic-only). The injection prompt stays the global `BOOTSTRAP_INJECTION_PROMPT`.
- `prompts.py` — all section header strings are imported from the sibling file.
- `schema` (`ContextData`, `ModuleInstructions`, `ContextRuntimeOutput`, `WorkingSource`) — provides the typed containers that flow through the pipeline.

**Consumed by:**
- `step_3_agent_loop.py` — the only caller that constructs and runs `ContextRuntime`. Its output (`ContextRuntimeOutput.messages`, `ContextRuntimeOutput.mcp_urls`, `ContextRuntimeOutput.ctx_data`) is forwarded to the agent framework adapter in subsequent pipeline steps.
- The package's `__init__.py` re-exports `ContextRuntime` under `xyz_agent_context.context_runtime`, but no other module within the package imports it at runtime.

## 设计决策

**Chat history comes from `ChatModule`, not from Event records.** The original design stored conversation turns as `Event` objects and reconstructed the message list from them during context assembly. After the 2025-12-09 refactoring, `ChatModule` (via `EventMemoryModule`) provides `ctx_data.chat_history` directly. The old `extract_narrative_data()` method and the Event History section of `build_complete_system_prompt()` are both commented out rather than deleted — they remain as documented fallbacks while the new approach is validated. This means there are dead code blocks with explicit `TODO` annotations; they are intentional placeholders, not forgotten debris.

**Dual-track memory split inside `build_input_for_framework()`.** Each message in `chat_history` carries a `meta_data.memory_type` tag set by `ChatModule`. Messages tagged `long_term` are placed as ordinary `role/content` pairs in the messages list (chronologically ordered, per-message truncation applied). Messages tagged `short_term` are serialised into the system prompt via `_build_short_term_memory_prompt()` under a dedicated markdown section. This separation exists because the LLM's context window treats the system prompt differently from the message history — short-term cross-topic context is better positioned as background framing than as fake conversation turns.

**Module instructions are deduplicated by `module_class`, not by `instance_id`.** A single module type (e.g., `JobModule`) can have multiple instances (one per job). If each instance contributed its own instructions section the system prompt would contain near-identical paragraphs. Deduplication at the `module_class` level ensures each module type contributes exactly one instruction block, taking its wording from whichever instance is seen first during iteration.

**Bootstrap injection is self-destructing.** The `Bootstrap.md` file is written once by the agent creator to seed initial behaviour. After three Event records exist for the agent, `context_runtime.py` deletes `Bootstrap.md` automatically on the next run. The threshold of three events is a deliberate grace period — the first few turns often include the bootstrap instructions being read and acted upon. If the agent fails to delete the file itself, the auto-delete prevents perpetual bootstrap mode without requiring external cleanup.

**`SINGLE_MESSAGE_MAX_CHARS = 4000`** is a per-message safety cap only. Overall context length management is delegated to the Claude Agent SDK's `MAX_HISTORY_LENGTH` setting. The two limits address different failure modes: per-message truncation prevents a single large paste from dominating the context window, while the SDK's history limit prevents total token overflow across many turns.

**`SHORT_TERM_TOKEN_LIMIT = 40_000` characters (≈ 10k tokens).** Short-term memory is intentionally given a smaller budget than the main message history. Groups are processed in reverse chronological order so the most recent cross-topic context survives budget exhaustion.

## Gotcha / 边界情况

**`run()` always appends the current user input as the final message.** The current turn's `input_content` (from `ctx_data`) is appended to `final_messages` after all history is inserted. If a caller accidentally includes the current turn in the `chat_history` they pass to `ContextRuntime`, the LLM will see it twice — once in the history position and once as the trailing user message. `ChatModule` is responsible for ensuring `chat_history` contains only prior turns.

**Auxiliary Narrative summaries are computed twice if `extract_narrative_data()` is disabled.** The commented-out `extract_narrative_data()` call would have populated `ctx_data.extra_data["auxiliary_narratives"]`. Because it is disabled, `build_complete_system_prompt()` has a fallback that extracts the same summaries directly from `narrative_list[1:]`. Any change to the auxiliary Narrative summary format must be applied in both places (the fallback block and the `extract_narrative_data()` method body), otherwise the two paths will diverge when `extract_narrative_data()` is eventually re-enabled.

**`evermemos_memories` enriches auxiliary Narrative summaries.** If the orchestrator layer passes `evermemos_memories` into `run()`, it gets injected into `ctx_data.extra_data` and later consumed inside `_build_auxiliary_narratives_prompt()` to append "Related Content" snippets. If `evermemos_memories` is `None` (the default), the section appears without enrichment and no error is raised. The enrichment path is Phase 3 functionality; leaving it `None` is the safe default.

**Bootstrap detection performs a raw SQL `COUNT(*)` query.** The Bootstrap injection block bypasses the Repository layer and issues `db.execute("SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s", ...)` directly. This is intentional to avoid pulling in `EventRepository` as a dependency, but it means the query is not covered by the standard repository test harness and will silently return `event_count = 0` if the query fails, which keeps the bootstrap prompt active longer than intended.

## 新人易踩的坑

The `run()` method's Step 1-1 comment says "Event selection disabled" and sets `messages = []`. This is not a bug — it is a documented transitional state. Do not "fix" it by restoring `extract_narrative_data()` without understanding that `ChatModule.hook_data_gathering()` in Step 1-2 is now the authoritative source of conversation history. Enabling both simultaneously would produce duplicate message history.

`ContextRuntime.__init__()` accepts a `database_client` parameter but falls back to `get_db_client_sync()` if none is provided. In test environments where no database is available, omitting this parameter produces a `DatabaseClient` that fails on the first `await` rather than at construction time — the same lazy-init gotcha documented in `database.py`.
