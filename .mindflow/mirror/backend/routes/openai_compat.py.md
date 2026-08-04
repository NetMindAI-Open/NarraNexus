---
code_file: backend/routes/openai_compat.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — _classify_event：剥空的回复工具事件整条丢弃

extract_reply_text 的空白守卫上线后，全 citation 回复抽出 `""`（falsy）
——若照旧落进 tool_call 分支，会把内部 MCP 回复工具名当真工具调用暴露
给外部 OpenAI-compat 客户端，且 content_emitted=False 触发误导性的
"upstream LLM error" no-reply fallback。现按三态返回值判定：非空 str
→ content；`""`（是回复调用但剥空）→ 丢弃整条事件；None（不是回复调
用，如 lark_cli 非发送命令）→ 照常 tool_call。不能按 is_user_reply_tool
名字判——lark_cli 同名工具既做回复也做非回复命令，按名判会吞掉后者
（tests/backend/test_manyfold_im_ingress.py 钉住该场景）。

## 2026-08-03(补2) — 非 @ 群消息短路到 `silent_ingest`

`is_mention=false && chat_type=group` 的 turn 在附件转换后短路:记忆
摄取 + 回执 completion(`_receipt_completion`,由 deny 回执助手改名而
来,deny 与 silent 共用),不构造 BackgroundRun。DM 的 is_mention=false
照常跑(平台不应发,发了也按普通 turn 容错)。

## 2026-08-03(补) — gate 放行后调 `convert_attachments`

managed turn 在 gate 通过后、run 启动前把平台附件 ref 并轨原生协议
(见 managed_channel_ingress.py.md 同日补条);never-raise,坏 ref
降级 text-only。

## 2026-08-03 — managed 业务门与收尾接线(trigger 执行体)

渠道 turn 在 BackgroundRun 构造**之前**过
`managed_channel_ingress.before_run`(wechat 认主、narramessenger
authorize;deny → `_denied_completion` 回执,两种 OpenAI 形状,agent
run 不启动)。流收尾的 finally(流式与非流式各一处)fire-and-forget
`after_run`(inbox 写、审计、错误兜底直发),配 done-callback(教训
#2);reply_text 取分类为 content 的片段拼接,error_text 取
last_error_msg。客户端断线时 finally 仍执行——inbox/审计不因掉线丢失。

## 2026-08-03 — 回复分类接声明链,`_REPLY_TOOL_NAMES` 硬编码退役

`_classify_event` 增加 `source_handler` 参数:回复识别与文本提取改为
`MessageSourceRegistry.get(working_source.value).extract_reply_text()`
——与 chat_module 拆分用户可见回复用的是**同一条 per-source 声明链**
(owner chat 落 default handler,行为等价于被删掉的硬编码三名单;渠道
turn 用各渠道注册的 extract_reply_fn,lark 的 `--markdown` 解析等免费
获得)。语义变化:reply 工具名命中但提取不到文本的调用,从"静默丢弃"
改为按 tool_call 展示(对 lark_cli 的非发送命令这是必须的)。
`_ensure_source_handlers_registered()` 在分类前 import MODULE_MAP 强制
注册,防"进程首个请求撞上空 registry"。无回复兜底文案按来源分叉
(`_no_reply_fallback`):MANYFOLD 保留诊断文案;渠道 turn 是中性回执
——agent 经本地渠道工具带外投递,无 owner 可见文本是正常结果(Q5 决策)。

## 2026-08-03 — managed-IM 分流(channel_provider/channel_context)

`ChatCompletionsRequest` 接收平台的 managed-IM 扩展字段;handler 在
run-job 短路之后、BackgroundRun 启动之前调
`build_inbound_run_context()`(见 manyfold/sync.py.md 同日条目),把
`working_source / input_content / trigger_extra_data` 三元组换成映射
结果。不带字段的请求与旧行为逐字节一致(有回归测试钉住)。
含义:known provider 的 turn 从"MANYFOLD 裸 chat"变为对应渠道的原生
inbound 语义(渠道模块按 working_source 渲染回复指令,agent 用本地
渠道工具直发,平台 agentManagedReply=true 时抑制自身出站不中继)。
后续阶段将接管回复分类(声明链)与渠道副作用,见
specs/2026-08-03-manyfold-managed-im-ingress-design.md。

## 2026-07-31 — _resolve_agent_creator 委托 AgentRepository.resolve_owner

包一层保 Optional 契约（'' → None），实现收敛到 repository seam。

## 2026-07-28 — run-job 控制消息短路

`chat_completions` 在提取 `user_input` 之后、构造 `BackgroundRun` 之前插入
一道短路：`parse_run_job_control(user_input)` 命中时不起 agent run，转而
`_run_job_completion()` → `execute_job_once()` 走 `JobTrigger._execute_job`
的执行体（副作用与 poller 拾取完全一致）。两种 OpenAI 形状都答：非流式直接
返回 outcome 文本；流式每 15s 发一个空 content 心跳，防代理和平台 idle
watchdog 掐断长 job。客户端断开**不**取消 job run（铁律 #14）——
`asyncio.shield` + done-callback 只负责取回异常。

匹配是**严格全匹配**（`_RUN_JOB_RE.match(user_input.strip())`）：只有整条
输入恰好是 `[[nx:run_job <job_id> v1]]` 才拦截，带任何多余文字都当普通对话，
所以正常用户消息不会被误伤。这段**有意不做 env 门控**——端点本身在
`ENABLE_MANYFOLD_API` 块内才注册且有 gateway-token 鉴权，`try_acquire_job`
兜底双跑。见 `[[manyfold/sync.py]]`。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-06-10 — `complete` 加入 `_TERMINAL_TYPES`

`BackgroundRun._finalize` 现在会向 live 订阅者广播终结
`{"type":"complete","state":...}` 帧（见 background_run.py.md 同日条
目）。本路由的 `_TERMINAL_TYPES` 把它加进去，让 SSE 流在收到该帧时立即
发 finish_reason="stop" + `[DONE]`，而不是等 broadcaster 关闭、迭代器
耗尽才结束。行为等价，只是更及时。

# openai_compat.py — OpenAI 兼容 chat completions（Manyfold 接入）

## 为什么存在

Manyfold 平台的 `ApiChatAdapter` 只会说标准 OpenAI 协议。本路由暴露
`POST /v1/chat/completions`，把一次 NarraNexus agent run 翻译成 OpenAI
SSE chunk 流，让外部平台无侵入地驱动 agent。

## 关键 Owner 决策（2026-05-25）

- 请求里的 `model` 字段 = **agent_id**（不是模型名）；所有 chunk /
  error 响应原样回显。
- 鉴权：Bearer `MANYFOLD_GATEWAY_TOKEN`（auth middleware 先行过滤）。
- 仅当 `ENABLE_MANYFOLD_API=1` 时注册（backend/main.py 条件 include）。

## 上下游

复用 BackgroundRun + Broadcaster：创建 run 后 subscribe broadcaster，
把事件按 `_classify_event` 映射到四个 OpenAI 通道——agent_thinking /
agent_response → `delta.reasoning_content`；
`send_message_to_user_directly` 的 args.content → `delta.content`；
其他 tool → `delta.tool_calls`；tool output → 非标准
`delta.tool_results` 扩展（Manyfold 端 openclaw.adapter.ts 配对消费）。
终结帧（`_TERMINAL_TYPES`）→ finish_reason="stop" + `data: [DONE]`。

## Gotcha

- 不映射的事件类型返回 None 静默跳过——新增广播帧类型对本路由默认
  无害，但若它是终结语义必须加进 `_TERMINAL_TYPES`。
- subscribe 必须发生在 run 启动后尽快完成，否则可能错过早期事件
  （见 L373 注释）。
