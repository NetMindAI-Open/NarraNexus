---
code_file: src/xyz_agent_context/agent_runtime/_agent_runtime_steps/step_3_agent_loop.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

TurnInput 组包新增 `agent_id=ctx.agent_id` 与
`expressive_tools=context.expressive_tools`(3.2 模块声明的投递面)。此前
适配缝空转:NexusPower 靠 server 名含 "chat" 猜回复工具、agent_id 恒
"agent"(2026-07-31 排查确认的生产缺陷)。

## 2026-07-30 (二次) — 兜底回复不许承诺没在做的事

`_FALLBACK_NO_REPLY_INSTRUCTIONS` 加两条规则:(1) 禁止任何"我来做 / 让我试试 /
稍等"式的进行中或即将开始的表述——**这条消息发出去,这一轮就结束了**,承诺永远
不可能兑现;(2) 当 `<this_turn_activity>` 显示 agent 只产出了意图、没有任何实际
结果时,必须直说没做成 + 给一条具体出路。同时把选 prompt 的内联三元表达式提成
`_fallback_instructions_for_mode(mode)`——这两段是**平台唯一替用户的 agent 张嘴
说话**的文本,值得有个能被测试钉住的接缝(见
`tests/agent_runtime/test_fallback_reply_honesty.py`)。

触发事件(2026-07-29 Jiaxi 报障):用户让 agent 看图写 Word,agent loop 一轮结束、
零工具调用,思考内容只有"我来用图像理解能力重新试试"。no_reply 兜底的指令是
"把 agent 本该说的话说出来",于是它忠实地把这句意图讲给了用户——用户等一份
没有任何东西在生产的文档。**一轮没干完是允许的**(铁律 #14 不强停、#15 不评判
模型);不允许的是**我们生成的文字**声称有活在干。区分点:管的是平台自己编的
话,不是模型的行为。

## 2026-07-30 — model_not_found 反哺探测嫌疑

fallback-skip 判定之后：raw_exception 路径直接看 `skip_reason_detail`，inline
路径扫 `ErrorMessage.action_reason`（response_processor 归因时写入），命中
`model_not_found` 就调 [[model_health]]`.report_agent_slot_suspect`（解析当前
agent slot 绑定→(source, protocol, model) 入嫌疑表，best-effort 永不抛）。
只有确定性 model_not_found 触发——余额/限流/5xx 归因不同，不会误伤。

## 2026-07-29 (二次) — helper payload 过滤原生回放行

`_build_helper_user_input` 的 history 过滤补两刀:role=tool 行(本就被排除)之外,
content 为空/None 的 assistant 行(原生回放的 calls-only 消息)也不进 prose
transcript——`str(None)` 曾会渲染出字面 `[assistant] None`。

## 2026-07-29 — 删除句柄机制(T5),−235 行

删掉的是:进程级并发闸门(`_resume_handles_in_use` + `threading.Lock`)、四重校验
`_resolve_resume_session_id`(叙事 / 指纹 / 工作路径 / 框架)、其包装
`_acquire_resume_session`、`_log_resume_cold`、lease 的 `try/finally`(退化为
`try/except`——那个 finally 存在的唯一理由就是释放 lease)、`resume_fingerprint()`
调用、`cli_config_fingerprint` 伴随字段、`TurnInput.resume_session_id` 传参。

**为什么整套都不需要了**:[[transcript]] 让 adapter 每轮自己写 transcript、用全新
uuid4 resume。于是没有存下来的句柄要查、没有东西会过期(校验的全部目的)、也没有
共享句柄会被两个 run 同时claim(lease 的全部目的)。

**T2 实测确认它在空转**:日志里 `resume decision: RESUME cli_session=…` 存的句柄,
正是我们上一轮自己生成的 uuid4(step_4 把它当 CLI 签发的存下来了),而我们每轮都
会覆盖它。四重校验算出什么都不影响结果。

顺带:**R5(叙事锚点降级)整项作废**。它要解决的是"叙事切换 → 校验不通过 → 冷启动",
而现在既没有校验也没有锚点。实测里那次白付 55,308 全价的形态,结构上不可能再发生。

## 2026-07-28 — 并发 resume 守卫：同一句柄同时只许一个 run 持有（review FIX 1）

同一 agent 的两个 run 可以并发（用户在聊，同 agent+owner 的 JobModule
trigger 同时触发）。R2 之前二者都是冷启动、无共享外部文件；R2 之后二者会
**解析出同一个 cli_session_id 并各起一个 `--resume <同一 id>` 的 CLI**，
两个写者共用一份 session JSONL。这种失败**不匹配** R3 兜底的
"No conversation found" 谓词，会直接以硬错误冒出——即本次 feature 自己引入的
新危害，必须在此处闸掉。

- 新增进程内守卫：`_resume_handles_in_use: set` + `_resume_handle_lock`
  （`threading.Lock`），键 = 表唯一键同一三元组
  `(agent_id, platform_session_id, framework)`。
  `_try_acquire_resume_handle` / `_release_resume_handle` 是 test-and-set /
  释放。**输者立刻冷启动**（`COLD reason=handle_in_use`），**绝不阻塞等待**
  ——等待会把 resume 从优化变成依赖、并让一轮卡在另一个长 run 后面（铁律 #14）。
- 为什么用 `threading.Lock` 而不是 asyncio.Lock / 裸 set：临界区是无 await 的
  test-and-set，asyncio.Lock 毫无收益还要像 [[db_factory]] 那样维护 per-loop
  注册表（asyncio 原语绑定创建它的 loop）；而本进程**可能有多个线程各自的
  event loop**（MCP 容器每模块一个线程 loop），危害与哪个 loop 驱动无关，
  所以守卫必须 loop 无关且共享。裸 set 单操作在 GIL 下原子，但 check-then-add
  这对不是，故取锁；锁持有微秒级、绝不跨 await，既不会死锁也不拖慢 loop。
- 新增 `_acquire_resume_session(...) -> (resume_session_id, lease_key)`：
  = 原校验闸门 `_resolve_resume_session_id`（保持纯校验、签名不变）+ 命中后
  才 lease。**lease 放在校验之后**：本来就不会 resume 的 run 不许挡住会
  resume 的那个。step_3 只许调这个 wrapper。
- COLD 日志格式抽成模块级 `_log_resume_cold(...)`，`handle_in_use`（在校验闸门
  之外决策）与闸门内八种 reason 共用同一形状，便于日志分析。
- **step_3 主体：resume 决策整块挪进 driver 那个 try**，末尾加 `finally` 释放
  lease。lease 必须**在 try 内**获取：若在 try 外获取，acquire 与 `try:` 之间
  任何抛错都会把键永久卡住（该 agent+session 在本进程余生里 resume 静默失效）。
  finally 覆盖四个出口——正常结束、`except Exception`、取消
  （CancelledError 属 BaseException，绕过 except 仍走 finally）、
  以及被弃用 generator 的 `aclose()`（GeneratorExit 落在 try 内的某个 yield）。
  释放函数**故意是同步的**：GeneratorExit 在飞时 await 会触发
  "async generator ignored GeneratorExit"，可能跳过释放。
- **连带修的真 bug**（否则上面的"airtight"是假的）：`@timed` 的
  asyncgen wrapper 用 `async for item in fn(...)` 转发，而 `async for`
  **不会关闭被迭代的 generator**——消费者 aclose 外层 wrapper 时，被包裹的
  step_3 只是挂着，它的 finally 要等 asyncgen GC finalizer 才跑（实测
  `aclose()` + `sleep(0)` 之后仍未释放）。已在 [[_timing.py]] 用
  `contextlib.aclosing` 修正：关闭立刻穿透。这条对**所有** `@timed` 异步
  生成器的 finally 清理契约都成立，不只 resume。
- **残留（已接受、fail-open）**：消费者用 `break` **丢弃**管线而不是关闭它
  （`agent_runtime.run()` 在取消时正是这么做的），`async for` 不会把关闭往下
  传，此时 finally 由 asyncgen GC finalizer 在一两个 loop tick 后跑（实测
  <10ms），而非同步。有界、自愈，最坏代价一次多余冷启动。若哪天要做到完全
  同步，需要在 `agent_runtime.run` 与 [[step_3_execute_path.py]] 两处的
  `async for` 上加 `aclosing`——本次刻意不动主管线取消路径。
- **刻意的局限**（措辞对齐 [[admission.py]]）：守卫是**进程内**的。云端今天
  orchestrator 单进程，一个守卫看得见所有 run；多副本部署需要按同一三元组
  做共享（Redis）守卫，上面两个 helper 就是那个缝。
- 测试：tests/agent_runtime/test_resume_concurrency_guard.py（lease 语义 +
  驱动真 step_3 验证正常结束/异常/中途 aclose 三条路径都释放，以及
  A 持有时 B 端到端冷启动）；tests/utils/logging/test_logging.py 补 aclosing 回归钉。

## 2026-07-28 — resume 决策 + TurnInput 注入（resume 化 R2/R3，dev 新结构重实现）

R1 只捕获；本条把查表/校验/注入接上（旧分支 be9c8ecd 的 step_3 部分在
dev 新结构上的重做——注入通道从裸 kwarg 换成 TurnInput 字段）：

- 新增模块级 `_resolve_resume_session_id(agent_id, session, framework,
  config_fingerprint, working_path, db_client)`（旧分支逐字移植）：开关
  （`settings.agent_loop_resume_enabled`）+ 句柄存在 + **三锚全符**
  （narrative / fingerprint / working_path）才返回存储的 cli_session_id；
  其余一律 None = 冷启动。**fail-open 到底**：查表/校验任何异常 → None +
  warning，优化永不打死轮次。铁律 #4：纯通用会话延续规则，无场景硬编码。
  每次决策恰好一条可 grep 日志：`[step_3] resume decision: RESUME …` 或
  `[step_3] resume decision: COLD reason=<flag_disabled|no_platform_session|
  fingerprint_unavailable|no_handle|narrative_changed|fingerprint_mismatch|
  working_path_changed|lookup_error:*> …`。
- 决策块置于 framework 解析之后、executor ensure 之前：canonical
  `cli_framework` 归一化与 `cli_config_fingerprint` 计算**上提到此处**
  （一次计算，决策与末尾 PathExecutionResult 组装共用；R1 原在组装处的
  重复计算段删除）。v1 只有 claude_code 走查询；codex 完全不碰。
- **TurnInput 构造移到决策块之后**（frozen dataclass，不能事后改），带
  `resume_session_id=`；driver_kwargs() 只在非 None 时发键（理由见
  [[turn_input.py]]——codex v2 的 ignored-kwargs WARNING 不被恒 None 字段
  刷屏）。
- PathExecutionResult 新增 `resume_failed=state.resume_failed` 透传——
  **无条件**（冷启动重试可能没报新 session_id，step_4 仍要删陈旧句柄）；
  CLI 句柄三伴随字段改为 `… if state.cli_session_id else None` 内联条件。
- 测试：tests/agent_runtime/test_resume_decision.py（九个决策用例）。

## 2026-07-28 — 不再现发会话票

step 3 开头那段「system-tier 运行就向网关 mint 一把 per-run key、注入
ClaudeConfig、finally 里吊销」的逻辑整段删除，连带 `gateway_unavailable`
的提前返回分支。

免费额度的凭据现在是用户 `user_providers` 里那张卡上的长期 key，和自带 key
走完全一样的 `provider_configs` 下发路径 —— step 3 对它没有任何特殊认知，
这正是目的。


## 2026-07-27 — driver 调用入参打包为 TurnInput（纯搬运）

3.4 组装 driver.agent_loop kwargs 的四个散落 local 收进
[[turn_input.py]] `TurnInput`，调用点改为
`driver.agent_loop(cancellation=..., **turn_input.driver_kwargs())`。
driver_kwargs() 复刻历史形状（含空值→None 归一），零行为变化。

## 2026-07-25 — PathExecutionResult 组装处补 CLI 句柄四字段（resume 化 R1）

组装前新增一小段：`state.cli_session_id` 非空时（只有 Claude 路径会报）填
`cli_framework`（framework_name 归一化到 canonical：claude→claude_code、
codex→codex_cli——存储键不能依赖用户 slot 恰好用了哪个别名）、
`cli_working_path=agent_working_path`、`cli_config_fingerprint` 经 ambient
`claude_config` 代理调 `resume_fingerprint()`。**指纹必须在 step_3 算**：本轮
的 per-task ContextVar 在此作用域保证还活着；step_4 不重算。fail-open：任何
异常 → None + warning，step_4 随之跳过持久化——resume 捕获永远不许伤害轮次。
本期只捕获不 resume（R2 的查表/注入还没接）。

## 2026-07-24 — 透传 `context.disallowed_tools` 到 driver kwargs（B++）

组装 driver.agent_loop kwargs 时新增 `disallowed_tools`（来自
[[context_schema.py]] `ContextRuntimeOutput.disallowed_tools`，即未绑定
channel 要求剔除的工具）。本地 SDK 侧与 WebSearch 守卫**合并**（见
[[xyz_claude_agent_sdk.py]]），remote 侧进请求体
（[[remote_agent_loop_driver.py]]）。codex driver 接受但忽略该 kwarg（本阶段
已知限制：codex 路径只有指令侧裁剪）。本文件纯搬运，无逻辑。

## 2026-07-23 — PathExecutionResult 透传 cache/num_turns(W1,纯搬运)

末尾组装 PathExecutionResult 时新增 `cache_read_tokens`/`cache_creation_tokens`/
`num_turns` 三项赋值(来自 state)。无逻辑变化;语义见 execution_state.py.md。

## 2026-07-23 — 免费额度网关会话票在此签发/作废（后端唯一正确的层）

免费额度改造：主钥匙只在 LiteLLM 网关容器，每次运行签一张会话票。**签票必须在本步
（后端 orchestrator）做，不能在 executor**——executor 跑用户可控代码、只收
`provider_configs`、绝不能持有网关 admin key。流程：驱动分发前调
`gateway_key_service.open_backend_session(db, agent_id)`：若 `provider_source=="system"`
就 mint 一张票并**写进 `ClaudeConfig` ContextVar**，随后
`executor_protocol.serialize_provider_configs()` 把它打包送到 executor，executor 只拿到
这张 scoped/可作废的票。返回 `(session, ok)`：`ok=False`（网关不可达/未配置）→ 直接
`yield ErrorMessage(error_type="gateway_unavailable", severity="fatal")` 并 `return`，
**绝不回退主钥匙、绝不用空占位 key 起子进程**。`session.close()` 在驱动 try 的 **`finally`**
里作废——run 生命周期界定、非定时器（铁律 #14）；非 system 运行整条链路是 no-op。硬崩溃
遗留孤儿由 executor-reaper 钩子回收（见 [[executor_reaper]]）。凭据细节见
[[gateway_key_service]]。

## 2026-07-22 — executor-infra 失败统一 surface + 审计 + try 边界上移

三处相关改动，收尾 OOM(-9/-6) 与 executor 不可达的"可读化 + 不被兜底掩盖 + 审计"：

1. `_record_oom_if_killed` → **泛化**为 `_record_executor_infra_event(db_client,
   user_id, error_type, error_str, output_already_emitted)`：用
   [[llm/failure.py]] `classify_executor_infra_failure` 判类，写
   `oom_killed`（-9/-6）或 `executor_unreachable`（[[executor_audit.py]]）。
   best-effort 永不抛，沿用原模式。
2. `_fallback_skip_decision` 返回**三元组** `(kind, reason, target_error_type)`：
   infra 命中先于 self-serviceable（typed/returncode 信号更确定）→
   `target=EXECUTOR_INFRA_ERROR_TYPE`；emit 分支按 target 选文案函数
   （`executor_infra_user_message` vs `self_serviceable_user_message`）。
   **OOM 从"故意 fall-through 到兜底"改为"surface + skip"**——不再被编造回复掩盖。
3. **try 边界上移**：`ensure_executor`/`wait_until_ready`/`get_agent_loop_driver`
   纳入同一 `try`。这样冷启动抛的 `ExecutorUnreachableError`（[[executor_errors.py]]）
   落到同一 except 走 infra 收尾，而不是逃出 step_3 变裸异常（issue ② 根因）。
   `PathExecutionResult` 结尾产出不受影响。
4. **severity 随"是否已回复"分级**（PR #133 review 连带修）：抽出
   `_has_organic_reply(agent_loop_response)`（复用到 `_should_run_helper_llm_fallback`）。
   infra/self-serviceable 的 raw_exception 分支：**若本轮已通过
   `send_message_to_user_directly` 回复过**（executor OOM/掉线可能发生在回复之后）→
   `severity="recovered_after_reply"`（warning 徽章、保留回复），否则 `fatal`。避免对
   已经拿到答案的用户显示"请重试"、也避免把"已回复但收尾失败"整轮记失败。配合
   [[loop/circuit_breaker.py]] 对 `infra_transient` 的熔断豁免，杜绝"平台抖动→冷却
   →拒掉用户按提示的重发"。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-14 — 兜底 skip 泛化:auth → auth + 确定性自助类（`_fallback_skip_decision`）

原本只有"inline auth 失败就 skip helper 兜底"一条（2026-06-11）。现在抽出纯
谓词 `_fallback_skip_decision(agent_loop_response, captured_error)`，把两条
error 路径都盖住:

- `("inline", None)`:`agent_loop_response` 里已有
  `error_type ∈ {auth_expired, config_actionable}` 的 fatal ErrorMessage
  （response_processor 在 loop 内已产出）→ 只 skip 兜底，不用再补消息。
- `("raw_exception", reason)`:loop 抛了 Python 异常，`captured_error` 有值
  但**还没有 ErrorMessage**，且 `classify_self_serviceable` 命中（类名保真，
  如 `ContextWindowExceededError`）→ skip 兜底 **并在此就地 yield** 一条
  fatal `config_actionable` ErrorMessage（否则该错误完全不可见）。
- `(None, None)`:非用户可修复失败 → 照常走 helper 兜底。

理由同 auth:context-window 这类确定性失败，agent 本体（工具/MCP/记忆）根本
没跑，兜底生成一条正常样子的回复是对事实的谎报——这正是"黑盒" P1 的根因。
分类器在 [[llm/failure.py]]，共享文案 `self_serviceable_user_message` 也在那，
避免 step_3 → response_processor 的循环导入。

## 2026-07-10 — `_resolve_agent_framework_name` 收缩为委托（单一 overlay）

原本这里手写了一份 agent_slots→user_slots 的 overlay。它现在**委托**给
[[providers/model_identity.py]] 的 `resolve_agent_model_identity(...).framework`——
同一份 overlay 既供 dispatch（选 driver）又供 prompt 的 "LLM Model" 行，二者不可能
再不一致。（PR #84：两份手抄 overlay 的判定曾漂移——prompt 侧漏了 `agent_framework`
非空这一条，在"有 provider 但 framework NULL"的 agent_slots 行上重新渲染出错误身份。）
`agent_runtime → agent_framework` 是合法 import 方向（本文件早已 import 该层）。
行为对 dispatch 不变，由 `test_resolve_agent_framework_per_agent.py` 兜底。

## 2026-07-09 — per-agent framework + owner bugfix

``_resolve_agent_framework_name`` is now keyed by ``agent_id`` (was ``user_id``).
It honours a per-agent ``agent_slots`` override that actually rebinds the agent
slot (has a ``provider_id`` — mirrors [[resolver]]'s overlay predicate so
framework and config never disagree), else falls back to the OWNER's
``user_slots`` (``agents.created_by``), else ``claude_code``. The call site was
fixed to pass ``ctx.agent_id`` instead of ``ctx.user_id`` — a latent correctness
bug: background triggers pass a trigger identity that isn't the owner, so the
framework could disagree with the owner-resolved config.

## 2026-06-18 — 冷启动 executor 先等就绪再驱动

冷启动分支（`ensured.cold_started`）发完 `executor.warming` UX 事件后,**先
`await wait_until_ready(executor_url)`(poll executor 的 /health)再驱动 loop**。
否则容器刚 `docker run` 起、uvicorn 还没起来,第一次连接撞冷启动 → 失败 → 错误地
落进 fallback(用户看到"醒来中"然后直接 fallback)。等就绪是 infra 等待,不是
agent-loop 上限(铁律 #14)。

## 2026-06-18 — executor OOM（exit code -9）审计可见性

`_record_oom_if_killed(db_client, user_id, error_str, output_already_emitted)`
模块级 helper，在 agent loop 的 `except` 捕获点被调用一次：若错误是
executor 子进程被 OOM-kill（`exit code -9`），best-effort 写一条
`oom_killed` 审计行（`instance_executor_audit`），供监测发现。**告警本身不在
这里做**——NarraNexus 开源，只产生信号（审计行 + `/admin/runtime/status`）；推
Lark 告警由 deploy 仓的 watcher 读这些信号去做（信号/告警分离,开源边界）。
**故意不做重试**——干净重试要求把流式 loop 改成可从头重跑，风险大，留作
后续专项（scheduling-resource plan）；今天 OOM 仍照常落入下方 fallback。
helper 绝不抛错（审计失败只 log），不影响 loop。

## 2026-06-11 — 鉴权失效时跳过 helper fallback（不伪造回复）

agent loop 出现 `ErrorMessage(error_type="auth_expired")`（response_processor
对 codex OAuth 过期等鉴权失败的归类）时，**跳过 `_stream_fallback_recovery`**，
不让 helper 编一个回复把"登录已失效"盖住（incident 2026-06-11：codex
refresh token 已用过 → 每轮静默退化到 gpt-5，用户以为"codex 变笨了"）。
此时 response_processor 已经发了那条 fatal、可操作的 re-login 提示，用户直接
看到它即可。

**坑（已避开）**：不能用 `return` 提前退出——后面还有必须 yield 的
`PathExecutionResult`（Step 4 靠它持久化本轮 Event）。所以是把 fallback 计算
+ `_stream_fallback_recovery` 那段包进 `if not auth_failed: ... else: log`，
auth_failed 时**继续 fall through** 到 sub-step 收尾 + PathExecutionResult。
`auth_failed` 通过扫描 `agent_loop_response` 里是否有
`error_type == AUTH_EXPIRED_ERROR_TYPE`（从 response_processor 导入常量）判定。

## 2026-06-10 — helper obtained via get_helper_sdk()

The fallback-reply stream no longer instantiates OpenAIAgentsSDK directly —
`get_helper_sdk()` (agent_framework/llm/helper_sdk.py) returns the per-task
helper (OpenAI or Anthropic Messages API) based on which helper config the
resolver installed. Call shape (llm_stream) unchanged.

## 2026-05-29 — pluggable driver + EverMemOS removed

The agent loop is now obtained via `get_agent_loop_driver(working_path=...)`
(framework registry, iron rule #9) — do NOT instantiate `ClaudeAgentSDK`
directly here; register a driver instead (see [[loop/driver.py]]).
The former EverMemOS episode await (`ctx.evermemos_task` → `relevant_episodes`
→ `context_runtime.run`) was removed.

## 2026-05-25 — Fatal-path recovery wired end-to-end (`_stream_fallback_recovery`)

The post-agent-loop recovery slot is now a single async generator that:

1. Drains the helper_llm stream as `AgentTextDelta` frames (when mode is `no_reply` or `after_error`).
2. Emits a synthetic `send_message_to_user_directly` `ProgressMessage` carrying `details.reply_via=helper_llm_{mode}` if any content streamed — downstream `chat_module._split_user_visible_response` picks this up like an organic reply, so persistence works without special-casing.
3. Yields the captured `ErrorMessage` LAST with computed severity (`recovered` / `recovered_after_reply` / `fatal`). The frontend reduces synthetic tool calls into `responseParts` first; yielding the error first would briefly flip `displayContent` to the error string before the synthetic lands.

The `except Exception` in the main agent-loop body **no longer yields** the ErrorMessage immediately — it stashes `{error_type, error_message}` into `captured_error` so the recovery generator can place it after the recovered reply. `_generate_fallback_reply_stream` now accepts the full context (system prompts + chat history + agent_loop_response + final_output + error_info) and uses one of two prompt templates (`_FALLBACK_NO_REPLY_INSTRUCTIONS` / `_FALLBACK_AFTER_ERROR_INSTRUCTIONS`); `_build_helper_user_input` assembles the user-input payload via tagged XML-ish sections so the LLM can navigate the context without re-instantiating the agent persona.

Rename: synthetic `details.reply_via` switched from `helper_llm_fallback` to `helper_llm_no_reply` / `helper_llm_after_error` so the UI can distinguish the two recovery modes. `chat_module` now copies any `helper_llm_*` tag onto the persisted row (was strict equality on `helper_llm_fallback`).

Contract is pinned by `tests/agent_runtime/test_fallback_streaming_order.py`.

## 2026-05-25 — Mode-aware fallback decision (`_should_run_helper_llm_fallback`)

Return shape changed from `(bool, str)` to `(mode | None, str)`:

- `"no_reply"` — chat turn ended cleanly without `send_message_to_user_directly`; helper_llm runs to write the missing reply.
- `"after_error"` — chat turn hit a fatal mid-stream and no organic reply was sent yet; helper_llm runs with full context (system prompts + completed tool results + error info) to produce a recovery reply. (Wired in T4.)
- `"partial_reply_then_error"` — fatal hit AFTER an organic reply; helper_llm does NOT run (we already spoke), but the caller surfaces a `recovered_after_reply` ErrorMessage. (Wired in T4.)
- `None` with `skip_reason` — `non_chat_trigger` / `cancellation_requested` / `already_replied_via_tool`.

The decision function is now the single point of truth for "what should this turn do at the recovery slot." Contract is pinned by `tests/agent_runtime/test_helper_llm_fallback_decision.py`.

## 2026-05-25 — Fallback prompt serializer added (`_serialize_agent_loop_for_prompt`)

Pure helper that renders `agent_loop_response` (raw runtime frames) into
a flat ordered plain-text block for the helper_llm fallback prompt. Sits
beside `_should_run_helper_llm_fallback` — both are no-IO, no-async, so
the recovery prompt assembly is unit-testable end-to-end without
spinning up the full async generator.

Per-entry cap defaults to 4 KB, total cap to 32 KB. When total exceeds
the cap, oldest entries drop first (with an `[... earlier activity
omitted ...]` marker) because the recovery reply needs recent activity
more than ancient setup. Adjacent `AgentTextDelta` frames coalesce into
one `[assistant_text]` block so the LLM sees coherent text instead of
the delta soup that's natural for streaming. This is the building block
for the bigger fallback-LLM-context redesign (fatal-path recovery with
full context; design is author-local).

Contract is pinned by `tests/agent_runtime/test_fallback_prompt_assembly.py`.

## 2026-05-13 — Phase B caller migration (generator-based ResponseProcessor)

`ResponseProcessor.process(...)` 在 Phase B 改成 generator。这里的 caller
从 `result = response_processor.process(response, state)` 改成 `for result
in response_processor.process(response, state):`——一个 raw event 可能
产生 0..2 个 ProcessedResponse（thinking 累积时是 0，非 thinking 事件
flush 残余 thinking 时是 2）。

同时在两个出口点（try 末尾 + except 中）调 `flush_pending(state)`——保证
stream 结束 / 异常退出时 batcher 里残留的 thinking 不丢。这是 batcher 设计
明确要求 caller 履行的契约。

## 2026-05-12 — Chat no-reply helper_llm fallback hardening

Self-review of the initial fallback (same-day) caught four real holes;
the fixes are pinned by
`tests/agent_runtime/test_helper_llm_fallback_decision.py`:

1. **Fatal error must skip the fallback**. If `agent_loop_response`
   contains an ErrorMessage with `severity="fatal"` (CLI timeout, SDK
   crash, etc.), `state.final_output` is partial reasoning; asking
   helper_llm to summarise that hallucinates a reply from a half-
   thought. chat_module's failed-turn path handles it instead.
2. **Cancellation must skip — and abort mid-stream**. If the user
   pressed stop, honouring the token is the whole point. The
   pre-check + a mid-loop check on the streaming iteration cover
   both "cancelled before fallback fires" and "cancelled mid-stream".
3. **`state.finalize()` runs before reading `state.final_output`**.
   The previous order read the unfinalized state.
4. **Partial-stream recovery**. If helper_llm errors after some
   deltas have already been yielded, the synthetic ProgressMessage
   is still emitted from `fallback_chunks`, tagged
   `details.fallback_partial=True` + `details.fallback_error`. The
   user keeps the visible deltas and chat_module persists the matching
   partial content — no half-reply + "decided not to respond"
   mismatch in DB.

The skip decision is factored into a pure function
`_should_run_helper_llm_fallback(working_source, agent_loop_response,
cancellation) -> (bool, skip_reason)` so the four guard cases can be
exercised by unit tests without spinning up the full async generator.

## 2026-05-12 — Chat no-reply helper_llm fallback (P0 #3)

After the agent loop completes, step 3 now inspects
`agent_loop_response` for a `send_message_to_user_directly` tool call.
When the turn was chat-triggered (`ctx.working_source == "chat"`) and
no such call exists, step 3 invokes the helper_llm slot via
`OpenAIAgentsSDK.llm_stream` and streams the resulting reply through
`AgentTextDelta` events — exactly the same channel the frontend uses
to render organic LLM stream, so users see the recovered reply in
real time without any frontend change.

After the stream completes, step 3 appends a synthetic
`send_message_to_user_directly` ProgressMessage carrying
`details.reply_via="helper_llm_fallback"`. Downstream:
- `ChatModule._extract_user_visible_response` picks the synthetic call
  up like any organic reply, so the assistant row persists the
  helper-generated text — NOT `io_data.final_output` (reasoning).
- `ChatModule.hook_after_event_execution` lifts the `reply_via` tag
  onto the persisted row's `meta_data.reply_via`.

Why this design (per 5/11 product review):
- `io_data.final_output` is internal reasoning, not speech (project
  iron rule: only `send_message_to_user_directly` counts as speaking).
  The previous "persist final_output directly" shortcut violated this.
- Only chat turns get the fallback. `message_bus` deliberately avoids
  replying to prevent agent-to-agent loops; job/lark/etc. have their
  own reply pathways.
- Streaming the helper_llm output keeps the user experience identical
  to a normal reply (no "blank then long pause then text" UX).

If the helper_llm call itself fails, step 3 logs and lets the
placeholder fall through — the honest record is "no reply" rather
than a silent leak of reasoning.

# step_3_agent_loop.py — Pipeline Step 3 Sub-path: Interactive Agent Loop

## Why It Exists

When `step_3_execute_path.py` routes to the `agent_loop` execution type, this module handles the full sub-pipeline for an interactive LLM-driven turn. It orchestrates sub-steps 3.1 through 3.5: context building, token budget computation, LLM invocation, tool execution, and response processing. This separation keeps the routing layer thin and the agent loop logic focused.

## Upstream / Downstream

**Called by:** `step_3_execute_path.py` — receives `ctx` and yields `ProgressMessage` + `PathExecutionResult`

**Calls:**
- `ContextRuntime.run()` (sub-step 3.2) — builds `ContextData` with all module data injected
- `ClaudeAgentSDK.agent_loop()` (sub-step 3.3) — drives the LLM turn via Claude Code CLI subprocess
- `ResponseProcessor.process()` (sub-step 3.5) — interprets LLM output into `ProcessedResponse`
- `ctx.module_service` — for hook calls between sub-steps

**Produces:** `PathExecutionResult` stored in `ctx.execution_result` by the calling router

## Key Design Decisions

### Sub-step Structure (3.1–3.5)
Each sub-step yields its own `ProgressMessage`. This gives the frontend granular visibility into long-running turns. The sub-step numbers appear in WebSocket progress events, allowing the UI to show "3.3 Calling LLM..." independently.

### skill_env_vars Extraction
`ctx_data.extra_data` is checked for `skill_env_vars` key after ContextRuntime runs. These env vars come from AwarenessModule and are passed directly to the Claude Code CLI subprocess. This is how agent-level tool permissions (e.g., allowed bash commands) propagate to the execution environment.

### Token Budget
Computed before the LLM call from `ctx.event.input_content` length and the loaded context. Budget calculation lives here, not in ContextRuntime, because it depends on the final assembled prompt length.

### Multi-turn History Injection
Chat history is injected into the system prompt (not as native multi-turn messages) because Claude Code CLI's `--system-prompt` flag doesn't support multi-turn natively. The `prompts.py` constants (`CHAT_HISTORY_HEADER`, etc.) wrap the history block.

## ContextData Mutations

| Field | What Happens |
|-------|-------------|
| `ctx_data` | Built fresh by ContextRuntime; not a pre-existing ctx field |
| `ctx.execution_result` | Set by router after this generator yields `PathExecutionResult` |
| `ctx.evermemos_memories` | Read here (cached in step 1); passed to ContextRuntime |

## Gotchas / Edge Cases

- **skill_env_vars missing key**: If AwarenessModule didn't populate `extra_data`, the dict lookup returns `None` gracefully — don't add a default, the SDK handles `None`.
- **ContextRuntime vs agent loop ordering**: ContextRuntime.run() must complete before agent_loop() starts; the context is not streamed incrementally.
- **Sub-step 3.4 (tool execution)**: Tool calls are processed inside `agent_loop()` via MCP — sub-step 3.4 in the progress messages is a checkpoint yield, not a separate function call.
- **ErrorMessage is appended to `agent_loop_response` AND yielded (Bug 8)**: the `except Exception` handler doesn't just push the error to the frontend — it also appends the `ErrorMessage` to `agent_loop_response` before moving on to `state.finalize()` and the `PathExecutionResult` yield. That append is what lets downstream hooks (ChatModule detects it in `hook_after_event_execution` and stores the failed turn with `meta_data.status="failed"` instead of a normal user/assistant pair) see the failure signal. Without the append, hooks see a silently-truncated turn and happily persist it as "success with empty reply", which was exactly the Bug 8 contamination.

## Common New-Developer Mistakes

- Trying to add module data gathering here: all data gathering belongs in `ContextRuntime` (which calls `hook_data_gathering` on each module). This step only orchestrates.
- Assuming `ctx.execution_result` is set inside this generator: the router (`step_3_execute_path.py`) sets it after intercepting the `PathExecutionResult` yield.
- Forgetting that `skill_env_vars` must be a `dict[str, str]` — passing any other type will cause the SDK subprocess to reject it silently.
