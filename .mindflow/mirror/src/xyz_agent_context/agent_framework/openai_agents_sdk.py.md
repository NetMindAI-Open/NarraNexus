---
code_file: src/xyz_agent_context/agent_framework/openai_agents_sdk.py
last_verified: 2026-07-09
stub: false
---

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
> 主链路的 `ResponseProcessor.append_text`,风险面大,单独立项处理(见
> `reference/self_notebook/todo/2026-07-08-codex-official-double-emit.md`)。这里只在
> helper 抽取侧做鲁棒化,保证结构化调用正确。

## 2026-07-03 — de-silence missing usage (Phase 0 / module H)

The three cost sites (`_record_cost` for the Agents-SDK path, the stream path,
the structured-fallback path) now branch: when a live cost context exists but the
provider returned 0/absent usage, call `cost_tracker.warn_missing_usage(...)`
instead of silently skipping. Turns an invisible accounting miss into an
auditable WARNING. The response_format ladder consumes no tokens on a failed
level (provider rejects before generating), so nothing is multi-counted — see
reference/self_notebook/token-accounting-audit-checklist.md.



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

# openai_agents_sdk.py — Helper LLM 适配层（结构化输出 + 兼容 think-block 模型）

## 为什么存在

Narrative 选择、Module 决策、数据提取等辅助 LLM 调用需要结构化输出（Pydantic model），而系统支持多种 OpenAI-compatible 端点（官方 OpenAI、NetMind、Yunwu、本地模型）。问题是不同模型对 `response_format` 的支持差异很大：minimax、deepseek 等会返回 `<think>...</think>` 推理块，无法直接解析为 JSON。这个文件提供统一的 `llm_function()` 接口，优先走 OpenAI Agents SDK 结构化输出路径，失败后自动降级到手动 JSON 解析路径，并通过 blocklist 机制避免对已知不支持结构化输出的模型重试。

## 上下游关系

被 `narrative/` 包（Narrative 选择决策）、`module/_module_impl/`（Instance 决策）等需要 helper LLM 的地方调用。调用者传入 `instructions`、`user_input`、`output_type` (Pydantic class)，拿回 result 对象后读 `result.final_output`。

配置读自 `api_config.openai_config`（ContextVar proxy），确保多租户并发安全。`model_catalog.get_max_output_tokens()` 提供每个模型的 token 上限。

和 `xyz_claude_agent_sdk.py` 的区别：这个文件处理有限上下文的"工具性调用"（决策、提取、分析），Claude SDK 处理无限 turn 的完整 agent loop。两者不互相调用。

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
