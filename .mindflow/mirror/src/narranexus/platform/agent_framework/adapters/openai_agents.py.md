---
code_file: src/narranexus/platform/agent_framework/adapters/openai_agents.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — `json_schema` 档发送 OpenAI strict 兼容 schema（`build_strict_json_schema`）

dev 日志 2026-08-25：helper 槽位切到 gpt-5.4-mini 后一天 104 次 400——`json_schema`
档带 `strict: true` 却发的是裸 `model_json_schema()`：对象没有
`additionalProperties: false`、带默认值的字段不在 `required` 里，OpenAI 的 strict 校验两条都拒。
阶梯随即把 `json_schema` 记成不支持、全部落到 `json_object`，阶梯最想用的那一档在 strict
provider 上永远到不了。

修法是**一个接缝**而不是逐个模型加 `extra="forbid"`：`build_strict_json_schema(output_type)`
= `agents.strict_schema.ensure_strict_json_schema(model_json_schema())`（openai-agents 的公开函数，
与 openai SDK 给 `beta.chat.completions.parse` 用的改写同源：关闭每个对象、全字段 required、
内联带兄弟键的 `$ref`、去掉 None 默认值；不用 openai 的私有模块，锁文件刷新不会变成启动
ImportError）。**只有 `json_schema` 档发它**；system prompt 里的 schema 提示、`json_object` /
纯 prompt 档仍是裸 Pydantic schema——带默认值的字段在那里依旧可省，弱模型不会被逼着编一个值，
客户端解析对多余键也保持宽容（首轮 review I4：统一口径会悄悄改所有 provider 的提示语义；
测试 `test_prompt_hint_keeps_the_raw_pydantic_schema` 与 `test_parse_stays_lenient_to_extra_keys_on_lower_rungs`
钉住两端）。

覆盖面：`tests/agent_framework/test_helper_strict_schema.py` 扫 `src/` 与 `plugins/*/src/`
里所有 `output_type=<Model>` 调用点（当前 16 个模型），逐个断言 strict 合规；再用假 client
断言实际发出的 `response_format` 是 `strict: true` + 改写后的 schema。

**改写会抛、不是 400（2026-09-10 二轮 review C1）**：`ensure_strict_json_schema` 对已带
`additionalProperties` 的对象节点（`dict[str, ...]` 字段、`extra="allow"`）直接
`raise agents.exceptions.UserError`——本地构造期异常，不是 provider 拒绝，`_is_response_format_unsupported_error`
认不出它。初版把 `build_strict_json_schema` 写在阶梯 list 的构建处（任何 try 之外），这类模型会在
发出第一个请求前就把整次 helper 调用炸掉，`json_object` / 纯 prompt 两档根本走不到。仓内 16 个模型
今天都干净，爆炸面是第三方 / marketplace / agent 自写插件经 `llm_function(output_type=...)` 传进来的
模型。现在阶梯的每一档是**延迟构造**（`(level, build_extra)`），`build_extra()` 在循环内自己的
try 里调：抛了就把该 `output_type` 记进 `_strict_rewrite_unsupported`（**按类型的限定名 + schema 指纹缓存**，键是
`module.qualname#<sha256(sorted model_json_schema)[:16]>` 字符串而非类对象，免得把插件按次造的类钉在内存里；
带指纹是复审 PR#392 M4：同模块两次 `create_model("Foo")` 形状不同时不再共用判决，形状相同仍合为一个键，
`test_a_same_named_model_with_a_different_shape_keeps_the_strict_rung` 钉住；且只有
`json_schema` 档的失败才入缓存，将来别的档位自带构造逻辑时不会顺手把 strict 档关掉；不是按
`(base_url, model)`——失败是 schema 自身的性质，若记到模型能力集上，其余 16 个合规模型在同一模型上
会一起被降到 `json_object`，正是 08-25 修复要够到的那档）、`logger.warning` + 审计事件
`strict_schema_rewrite_rejected`（走既有 `_audit_framework_downgrade` 通道，不静默），然后 `continue`
到 `json_object`。`_do_call` 那个 try 保持只处理网络/provider 错误，没有放宽。测试
`test_rejected_strict_schema_degrades_to_json_object`（只发一次请求、落 `json_object`、审计一次、二次调用
不重建）与 `test_rejected_output_type_does_not_demote_the_model_for_others`（同模型上 `ContinuityOutput`
仍走 `json_schema`）钉住；把 try 去掉即红。

`from agents.strict_schema import ensure_strict_json_schema` 挪进 `build_strict_json_schema` 函数体
（二轮 review M2）：模块级 import 把整个 `agents` 包（~190 模块、实测 0.8s）拉进每个加载本适配器的进程
（backend / MCP module server / worker），而本文件其余 `from agents import ...` 本来就都是调用期导入。

## 2026-09-07（批 1 三轮复审移植）— 空 slot 的旧行为如实记录

旧代码 `is_default = (model == "default")` 对空串为假，两条分支都 `return openai_config.model`，即把**空模型 id**
发给上游（400）。现在空串与 `"default"` 哨兵同义——这是模型解析合一时唯一一处有意的可观察行为变化，docstring
已写明；`tests/nx_kernel/contracts/test_resolve_model.py` 钉住新行为。

## 2026-09-03（批 1）— 模型解析合一

`_resolve_model` 改为调用契约层 `resolve_helper_model(honour_requested=True)`；旧 docstring 的「三种模式」中模式 2/3 返回值相同，「官方端点 vs 自定义端点」判断从未影响结果，`_OFFICIAL_OPENAI_BASE_URLS` 已连同它删除（零其他引用）。唯一行为差异：slot 为空串时以前直接回落 `OpenAIConfig.model`，现在与 `"default"` 哨兵一致先取调用点偏好——这是三份实现合一后的统一语义，`tests/nx_kernel/contracts/test_resolve_model.py` 钉住官方/自定义端点同输出与空 slot 行为。

## 2026-08-25 — helper 调用子相位计时（诊断埋点，无行为变化）

`llm_function` 里把一次 helper 调用拆成子相位计时，回答「一次 helper LLM 慢在哪」：
是模型/网络（`llm.helper.api_agents_sdk` 或 `llm.helper.api_fallback`），还是 SDK 包装
开销——每次新建 `AsyncOpenAI` 客户端的 `llm.helper.client_build`、记账写库的
`llm.helper.record_cost`。外加入口一条 `[HelperTiming] llm_function start` INFO 打出
`model / input_chars / output_type`。**纯 `timed()` 包裹既有 `await` + 一条 log，零控制
流改动**。缘起：叙事门 continuity/unified-match 两个 helper 在生产被感知「十几秒」，而裸打
litellm 只有 2-3s，怀疑差距在 SDK 包装层（连接不复用/记账）——此埋点用来坐实。

## 2026-08-03 — 接上 [[_prompt_probe]]（默认关闭的诊断挂点）

`_run_agent` 在 `self._resolve_model(model)` 之后调一次 `_probe_emit("openai",
model_name, instructions, user_input)`。**无行为变化** —— 探针默认关闭，且门在做任何
哈希/栈回溯之前就判掉。

**为什么挂在这里而不是别处**：这是本 SDK 唯一一个同时握有「最终 model_name」和
「instructions / user_input 各自的原文」的点。二者必须同时在手才有意义 —— 探针要回答的
问题是那约 6 次 helper 调用有没有 ≥4096 token 的相同前导，而
`instance_decision` 把 85 字符常量当 instructions、18K 全塞 user_input 这个事实，
只有看到这个切分才暴露得出来（`instructions` 映射到 `system`，所以「给 system 加
cache_control」实际会去缓存那 85 个字符）。三个 helper SDK 各在自己的同类位置挂一次。

import 是**模块级**的，与 [[cli_helper]] / [[anthropic_helper]] 一致。初版写成函数内
import，实测 `llm._prompt_probe` 与本模块之间无循环依赖，纯属不一致，已统一。

## 2026-07-21 — 新增对外共享的 `json_repair_note()`(Lark bug #2)

在 JSON 抽取工具旁(`_extract_json_from_llm_output` / `_first_balanced_json`)新增
`json_repair_note(reason)`,生成一条"你上一轮不是合法 JSON,请只输出纯 JSON 对象"的
re-prompt。这是**对外契约**:两个 Claude helper SDK([[anthropic_helper]] /
[[cli_helper]])在结构化输出抠取/校验失败时,用它做有界修复重试。放在本文件是
因为本文件已 owns 抠取逻辑,helper 侧只需复用。提示保持泛化(无场景专有词,铁律 #4)。

## 2026-07-09 — 平衡扫描精确化(PR review Minor)

抽出 `_balanced_end(text, start)`:用**类型化配对栈**(`{`↔`}`、`[`↔`]`)而非共享
depth 计数,`{"a": 1]` 这类错配会被判为不平衡(而非"平衡但随后 loads 失败")。
`_first_balanced_json` 改为**遍历每个 opener**:某个候选虽平衡但 `json.loads` 失败时,
继续找下一个 opener(不再一失败就 return None),对象内含数组等嵌套仍整段捕获。

## 2026-07-08 — `_extract_json_from_llm_output`: 首个平衡对象兜底

抽取器原本只做贪婪匹配(首个 `{` → 末个 `}`),单个**嵌套**对象要靠贪婪才能整段
捕获。但 codex CLI helper 的流会把消息**重复两遍**(流式增量 + item.completed 全量
副本,措辞有时略有出入),于是文本里是 `{...}{...}` 两个拼接对象,贪婪跨到最后一个
`}` → 整段 `json.loads` 失败 → 原来直接返回 None,结构化 helper 调用全挂。

新增 `_first_balanced_json`(带字符串/转义感知的括号配平扫描)作为**兜底**:仅在贪婪
路径解析失败后才跑,返回**第一个**平衡的 `{...}`/`[...]`。纯增量、零回归(既有能解析
的输入行为不变)。回归测试见 `tests/agent_framework/test_json_extraction.py`。

> 注:文本重复的**根因**在 codex_official translator 对 `agentMessageDelta`(增量)
> 与 `item.completed`(全量)双重 emit 成 `response.text.delta`——这条也影响 agent
> 主链路的 `ResponseProcessor.append_text`,风险面大,单独立项处理(author-local todo)。这里只在
> helper 抽取侧做鲁棒化,保证结构化调用正确。

## 2026-07-03 — de-silence missing usage (Phase 0 / module H)

The three cost sites (`_record_cost` for the Agents-SDK path, the stream path,
the structured-fallback path) now branch: when a live cost context exists but the
provider returned 0/absent usage, call `cost_tracker.warn_missing_usage(...)`
instead of silently skipping. Turns an invisible accounting miss into an
auditable WARNING. The response_format ladder consumes no tokens on a failed
level (provider rejects before generating), so nothing is multi-counted (verified in the token-accounting audit,
2026-07).

## 2026-05-29 — self-downgrade events audited to DB (E2)

`_audit_framework_downgrade(event_type, detail)` writes permanent
downgrade events to the `service_audit` table (service="llm_framework")
so they survive docker restart and are queryable (incident lesson #4/#5).
Fired at: blocklist add (`agents_sdk_blocklisted`) and `_mark_unsupported`
(`response_format_level_unsupported`). Best-effort, never raises.

## 2026-05-29 — A1/A2 (blocklist isolation + slot model)

Blocklist keyed by (base_url, model); only a clear "unsupported
response_format" error blocklists (transient/5xx never do — lesson #3).
`_resolve_model` honors the user's concrete slot model over a call-site
hint (rule #15; hints are OpenAI-catalog names that 404 on third-party
endpoints).

## 2026-05-28 — `_fallback_chat_completion` 升级成 3 层 response_format 阶梯

Production bug：NarraNexusPM agent 02:39 那次 `narrative.continuity_detect`
在 DeepSeek-V4-Flash 上返回 `structured=fallback_first_fail`，导致继承的
fallback 走纯 prompt-engineering 路径，正则抠 JSON 偶尔抠出错误内容。

修改：fallback 路径不再"光靠 prompt + 正则"，而是**3 层 response_format 阶梯**，
按可靠性从严到松依次尝试：

| 层级 | 含义 | 谁支持 |
|---|---|---|
| `json_schema` (strict) | API 层强制返回符合 Pydantic schema 的 JSON | OpenAI / V3.1 / V3 / Together / Anyscale |
| `json_object` | API 层强制返回任意有效 JSON object | 上面那些 + **V4-Flash / V4-Pro** |
| (无 response_format) | 当前的纯 prompt-engineering 路径 | 兜底 |

每个 `(base_url, model)` 的能力缓存进 `_response_format_capability: dict[tuple, set[str]]`。
某一层在某模型上抛 "response_format type unavailable" 之类 → 那一层从该模型的
集合里 drop，后续调用不再试。Transient 错误（rate limit / 5xx / network）
**不** downgrade 层级，原样 re-raise。

NetMind 实测（`tests/agent_framework/_manual/probe_response_format.py`）：
- V3.1 三层全支持，停在 `json_schema`，1 API hop
- V4-Flash / V4-Pro `json_schema` → 400 "unavailable"，自动 fall to `json_object` 成功，
  cache 学到后续只 1 hop
- V3 三层全支持但响应包 ```json``` fences，`_extract_json_from_llm_output` 已经能剥

测试覆盖：
- `tests/agent_framework/test_structured_fallback_ladder.py` — 11 个单元测试，
  mock OpenAI client 覆盖每一层降级路径 + cache + 错误分类
- `tests/agent_framework/_manual/smoke_*.py` — 真打 NetMind 的 smoke
  test（需要 `NETMIND_API_KEY` env，不进 CI 默认 run）

## 2026-05-27 — `_last_llm_call_info` ContextVar 添加 `response_format` 字段

3 层阶梯落到哪层会被 `_fallback_chat_completion` 记到 ContextVar 上
（`{"structured": ..., "response_format": "json_schema" | "json_object" | "prompt_only"}`），
调用方 / `timed()` tag 能读出来。

# adapters/openai_agents.py — Helper LLM 适配层（结构化输出 + 兼容 think-block 模型）

## 为什么存在

Narrative 选择、Module 决策、数据提取等辅助 LLM 调用需要结构化输出（Pydantic model），而系统支持多种 OpenAI-compatible 端点（官方 OpenAI、NetMind、Yunwu、本地模型）。问题是不同模型对 `response_format` 的支持差异很大：minimax、deepseek 等会返回 `<think>...</think>` 推理块，无法直接解析为 JSON。这个文件提供统一的 `llm_function()` 接口，优先走 OpenAI Agents SDK 结构化输出路径，失败后自动降级到手动 JSON 解析路径，并通过 blocklist 机制避免对已知不支持结构化输出的模型重试。

## 上下游关系

被 `narrative/` 包（Narrative 选择决策）、`module/_module_impl/`（Instance 决策）等需要 helper LLM 的地方调用。调用者传入 `instructions`、`user_input`、`output_type` (Pydantic class)，拿回 result 对象后读 `result.final_output`。

配置读自 `api_config.openai_config`（ContextVar proxy），确保多租户并发安全。`model_catalog.get_max_output_tokens()` 提供每个模型的 token 上限。

和 `adapters/claude/sdk.py` 的区别：这个文件处理有限上下文的"工具性调用"（决策、提取、分析），Claude SDK 处理无限 turn 的完整 agent loop。两者不互相调用。

## 设计决策

**运行时 blocklist**：`_structured_output_blocklist` 是进程级 set，第一次遇到结构化输出失败的模型就加入，后续所有调用直接跳 SDK 走 fallback。这样不需要配置文件，自动适应新模型。缺点是 blocklist 不持久化，进程重启后会重新尝试一次 SDK 路径。

**`_resolve_model()` 的三种模式**：`"default"` sentinel 值允许调用方指定 per-call 的模型名（官方 OpenAI 多 model 场景）；指定具体 model 且官方端点时强制用该 model；非官方端点时总用 slot 配置的 model（代理端点往往只支持特定模型名）。

**`max_completion_tokens` vs `max_tokens` fallback**：先试 `max_completion_tokens`（新 API），如果 provider 报错再 fallback 到 `max_tokens`（旧 API）。这是为了兼容不同 provider 的 API 版本差异。

**`_extract_json_from_llm_output()` 的穿透逻辑**：先剥 `<think>` 块，再剥 markdown code fence，再用正则找最外层 JSON object/array。能处理大多数"乱七八糟"的 LLM 输出，但对嵌套结构不规范的输出可能误提取。

## Gotcha / 边界情况

- blocklist 是进程级全局变量，一个用户触发的模型失败会让所有用户的该模型都走 fallback 路径。这在单模型多用户场景下是期望行为，但如果不同 provider 用同一个 model name 则可能误 block。
- `_SimpleResult` 和 `_ParsedResult` 是私有包装类，调用方不应该直接 isinstance 检查它们。

## 新人易踩的坑

- `result.final_output` 在没有 `output_type` 时是字符串；有 `output_type` 时是 Pydantic model 实例。两种情况的类型完全不同，调用方需要根据是否传了 `output_type` 来决定如何处理返回值。
- 测试时如果用假的 `openai_config.base_url`（非官方端点），`_resolve_model` 会强制用 slot 配置的 model name，即使你传了其他 model 名也不生效。


## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
