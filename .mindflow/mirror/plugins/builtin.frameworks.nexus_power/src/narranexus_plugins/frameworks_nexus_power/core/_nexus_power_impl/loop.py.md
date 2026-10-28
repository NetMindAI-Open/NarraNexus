---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/loop.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

新增第四种「先修请求再重放」：`IMAGE_INPUT_REJECTED`。用户选的纯文本模型（铁律 #15，平台不干预选型）
遇到截图会回确定性 400；若不处理，本轮之后每一步都会再撞，平台就成了打断源。修复条件三者同时成立：
分类为 IMAGE_INPUT_REJECTED、本轮尚未启用、**被拒请求里确实带了图片**。启用后 `_build_request` 对
本轮剩余所有请求把 image_url 部件换成 `IMAGE_WITHHELD_NOTE`（陈述事实：模型看不到这张图），只改请求、
不改 ledger。只武装一次；没带图却报这个错说明不是图片问题，直接诚实失败，不做字节相同的重试。
实测（2026-09-24）：NetMind 对 DeepSeek-V4-Pro 是静默吞图不报错，模型自述看不到；此修复针对会直接
400 的 provider（DeepSeek 官方、OpenAI 文本模型、vLLM 等）。

## 2026-09-10（B-05/#127）— `_fail` 自报 `fatal = not self._turn_delivered()`

`_fail()` 的 TYPE_ERROR payload 带 `"fatal"`，契约正文只在 [[response_processor]]（终局 **且** 未交付）。
本框架的判据 `_turn_delivered()` 两条腿：
- 本 turn 见过表达型调用（`_turn_expressed`，DISPATCH 维护）→ 已交付；
- 或本 turn 有**已提交**的 plain-text 回复（`_turn_text_streamed`）→ 已交付。它只在 run_turn 里某个 step
  **提交成功那一刻**（`if error is None:`，`step_meta` 每次 attempt 都 clear，所以必须就地读）置位，条件是
  该 step 有文本（`had_text`）**且此刻**没有任何表达工具（`expression.names()` 为空，team patrol 这类
  plain-text turn，平台直接投递正文，见 harness/expression.py 文件头）。
  - 不在 delta 到达时置位：中途断掉的 attempt 的半句会被 `discard_step()` 丢弃，没交付就不能算。
  - `names()` 在提交时快照而不是在 `_fail` 时读：`expand()` 可在 turn 中途授予表达工具（DISPATCH 在提交
    之后），失败时刻再读会把之前已作为正文交付的文本事后判成 monologue。
有表达工具时文本是 monologue、不投递给任何人，**不**算交付。`_turn_delivered()` 只是两个布尔的或。

测试：`test_fail_after_an_already_expressed_reply_is_not_marked_fatal`、
`test_fail_with_no_prior_expression_is_marked_fatal`、
`test_plain_text_turn_that_already_spoke_is_not_marked_fatal`、
`test_plain_text_turn_that_never_spoke_is_still_fatal`、
`test_monologue_text_on_an_expressive_turn_does_not_count_as_delivery`、
`test_plain_text_discarded_by_a_broken_attempt_is_not_delivery`、
`test_plain_text_committed_before_a_mid_turn_grant_is_still_delivery`、
`test_plain_text_after_a_mid_turn_grant_is_monologue`。

## 2026-09-10（B-03）— 空产出 + `max_tokens` 不再被 STOP_CHECK 误判成 NO_MORE_ACTIONS

**问题形状**：MODEL_STREAM 成功返回，但这个 step 零文本、零工具调用，`stop_reason`
落在 `_TRUNCATING_STOP_REASONS`（`"length"`/`"max_tokens"`）——不是「模型无事可做」，
而是默认思考的模型把 CoT 全部吃进了 output budget，答案还没写出来预算已见底
（NetMind DeepSeek-V4-Pro 实测）。原来这个形状落进 STOP_CHECK，当作
`EndReason.NO_MORE_ACTIONS` 静默收工，final_output 与 error_message 双空
（2026-09-08 triage：25-45% 平台 job run 这样消失）。判「零文本」靠 `_stream_step`
新增的 `step_meta["had_text"]`（每个非空 `text_delta` 置位），不是只看 `step_calls`
——后者会漏掉「吐了字但没调工具」的真实产出。

**处理**：在 POST-STREAM 取消边界之后、DISPATCH 之前判这个形状。

- 真正修好事故的是 [[profiles]] 的思考地板（1_024 → 8_192）。本分支只是兜底重试。
- 重试 = 地板乘数翻倍后重放整个 step，仅一次（`_truncation_retried`，与
  `_continuation_turn`/`_expression_nudged` 同一「修复只武装一次」纪律）。乘数走
  `ModelRequest.floor_multiplier`（[[model]]），**不**写进整个 turn 共享的
  `a.params.extra`。
- **只有能真正加大请求才重放**：`current_budget` / `new_budget` 都由 [[profiles]] 的
  `requested_max_tokens`（client 实际发送值的唯一来源）按当前乘数 / 乘数 ×2 求出，
  `new_budget > current_budget` 才重放；否则直接
  `_fail(OUTPUT_TRUNCATED)`，不重放一个逐字节相同、注定又空的请求。两种「翻不动」：
  ceiling 已等于地板——catalog 里四个 `thinks_by_default=True` 的模型（V4-Pro/V4-Flash/
  o3/o4-mini）ceiling 全是方言默认 8_192，等于思考地板，所以**对这四个模型重试永远不触发**；
  或用户在 `params.extra` 钉了 `max_tokens`（`requested_max_tokens` 原样返回钉住值，两次求值
  相等）。后者今天无触发面（平台没有任何代码往 `llm_extra` 写 `max_tokens`）。失败文案报
  `current_budget`，即实际发出的值。
- 失败的 error_type 是 `OUTPUT_TRUNCATED`，[[event_adapter]] 把它映成平台的 `OUTPUT_BUDGET_EXHAUSTED_ERROR_TYPE`（[[runtime_message]]），熔断据此不记账；文案不再承担任何匹配职责
  （[[circuit_breaker]]）。重放是 `continue` 回外层循环，由外层重建 request，这里不重复构建。
- 重放前调 `ledger.discard_step()`：空 step 在 `_turn_messages` 里什么都没留下，但它的
  `_step_thinking` 没被 `_fold_step_message` 的 early return 清掉；不 discard 的话，
  重放那一步的 CoT 会拼到废弃 CoT 后面一起进 `reasoning_content`（deepseek 行
  `thinking_replay="keep"` 会原样回传，provider 要的是产生本次 tool call 的那段 CoT）。
- **乘数只服务被重放的那一步**：截断判定一过（该 step 有产出），乘数复位为 1。地板刻意
  压过 headroom（见 [[profiles]]），粘性乘数会让本 turn 后续每一步（input 只增不减）都
  带着放大后的地板越墙。`_truncation_retried` 不复位，所以同一 turn 再次截断直接失败，
  不会循环。
- 翻倍后仍空 → `_fail(OUTPUT_TRUNCATED)` 真失败，不无限重试（铁律「不设硬顶」指不因慢/久
  砍轮次，不是连续两次零进展也继续喂钱）。
- 取舍：这个判定在 DRAIN_STEERING 之前且重放直接 `continue` 回 PROJECT，被截断那一步期间
  到达的 live-steering 消息会晚一次 model call 才被 drain（不丢，只是延后；每 turn 至多一次）。

测试（`tests/nexus_power/test_loop_e2e.py`）：
`test_truncated_empty_step_retries_with_doubled_budget_then_succeeds`、
`test_floor_multiplier_resets_after_the_retried_step_succeeds`（乘数序列 `[1, 2, 1]`）、
`test_truncated_empty_step_fails_after_one_retry`、
`test_truncation_with_a_pinned_max_tokens_fails_without_replay_and_reports_it`、
`test_a_brief_but_real_answer_is_not_treated_as_truncation`（负例）、
`test_truncation_fails_immediately_when_the_real_ceiling_cant_grow`（真实 V4-Pro profile）、
`test_replayed_step_after_truncation_does_not_concatenate_reasoning`、
`test_truncation_failure_message_carries_the_breaker_exemption_marker`。

## 2026-09-03（批 2a.5）— 模型请求用 `model_tools()`

发给模型的 tools 数组改取 `a.tools.model_tools()`（deferred 不上榜）；其余路径仍看 `visible_tools()`。

## 2026-08-13（管线审后）— expressed 记账走契约裁决器、只数 parse-valid

置位从 DISPATCH 首行移除：截断参数的回复调用是「回答不执行」，零投递却标已表达，
nudge 在它最高概率的触发场景（长 speak 参数撞 output 上限）失效（管线审 I#1）。
现在 STOP_CHECK 前用 `expression.turn_had_expression([c for c in step_calls if
c.parse_error is None])` 一次性算——语义回归 ExpressionContract 唯一裁决器；
hook-denied 仍算已尝试（nudge 不该怂恿重试被禁工具）；记账在 steering drain 之前，
steering continue 不丢账。突变锁 test_expression_nudge_fires_when_the_only_reply_call_failed_to_parse。

## 2026-08-13 — mute-turn nudge（opt-in，语音轮）

STOP_CHECK 关轮前：若 `assembly.expression_nudge` 开且本轮零 expressive 调用而
表达面非空，注入一条 steering 提醒（prompts.expression_nudge，点名默认回复工具）
再给一步；至多一次（`_expression_nudged`），nudge 后仍沉默则正常收轮——只加步、
不锁死、不 force-stop（铁律 #14）。默认关：群聊/bus 的合法静默不受影响。动机：
8/13 对抗实测 1/22 语音轮对乱码 STT 输入闷声（'纳指鸡'），语音轮无回复=坏结局。
expressive 见闻在 DISPATCH 处累计（`_turn_expressed`）。

## 2026-07-31 — 截断措辞不再被 stop_reason 一票否决

unparsed_call_result 现在主要看 call.truncated(字节证据),stop_reason 降级为佐
证(_TRUNCATING_STOP_REASONS 增补 "max_tokens",Anthropic 原生词汇,原来只认
OpenAI 的 "length")。两条自救方向是相反的——「发小一点」vs「发对一点」——所以
点错比不说更糟:网关把被切断的调用报成 tool_use,模型据此判定是转义问题,原样重
发同一个 15KB 调用,连撞三轮直到 turn 结束(agent 初号机,dev 2026-07-30)。

## 2026-07-31 — prefill 被拒:补一轮续写 user 消息,只补一次

某些后端拒绝以 assistant 结尾的对话。真 Anthropic 接受,所以**原样发,撞了再
修**——网关那种无条件预防式改写会对本来能接受的后端也白付代价。
PREFILL_REJECTED 在 MODEL_STREAM 的错误分支里拦下(和 CONTEXT_OVERFLOW 同一位
置、同一形状:修请求→重放 step),置 _continuation_turn 后 _build_request 在投影
结果**后面**追加 CONTINUE_PREFILL 用户消息。

三条纪律:①只武装一次——反复追加就是自旋,而且每份都在叠加同一句指令。注意这是对
**修复**的约束,不是对错误的判决:这类 400 多数在说「哪个后端接的」而不是「我们发了
什么」(2026-07-31 实测:失败那轮的真实形状重放三次全 200),所以修复用完之后仍然交给
重试策略,不是当场判死;②追加的消息是
**传输层修复不是轮次历史**,不进账本、不带到下一轮;③**武装 ≠ 每次都贴**——追加
必须再判一次「投影确实以 assistant 结尾」。`_build_request` 一个 turn 内会被调用
多次(每 step 一次、压缩重试一次、prefill 修复一次),只看标志位会在修复成功之后
继续贴:那时投影结尾已是 tool result,这句「接着上次说、不要复述」是**假的**,还会
压掉工具返回后本该有的正常表述;Anthropic 方言下 tool_result 本身装在 user 消息里,
再贴一条就是连续两个 user,目前只靠 litellm 合并同角色消息才没炸。

## 2026-07-30 — 残缺参数的调用「被回答」而不是被执行

参数 JSON 没解析成功的 tool call(典型:输出 token 上限把 arguments 流切断,
finish_reason=length)在 DISPATCH 里短路:不过 hook、不进通道,直接合成错误
result 回给模型。措辞工具无关(铁律 #4)且明说自救路径——length 停机时点名
「输出上限 N 切断了参数,请分多次小调用/用编辑工具增量扩展」;业界实测弱模型
拿到裸 parse error 不会可靠自修复(codex#19765)。stop_reason 经 _stream_step
的 step_meta 出参带到 DISPATCH(tool_use 事件先于 done 到达,截断上下文只能
事后补)。旧行为=带着 {} 参数执行,write_file 落到 workspace 根报
「Is a directory」,把模型引向路径排查死胡同(2026-07-30 事故)。

## 2026-07-30 — tool_use_start 不再被 continue 吞掉

`_stream_step` 原来在 `include_arg_deltas` 分支里对 tool_use_start 做完
extractor 设置就 `continue`，事件到不了账本。现在设置完落到
`ledger.record_model_event`，由账本发「名字先行」ui 事件（TYPE_TOOL_USE_START）。

# loop — 相位推进器(≤500 行门禁)

## 2026-07-30 — 流内取消:显式 aclose + 后流边界

`_stream_step` 每个模型事件前查 cancel,命中即 `await stream.aclose()` 再 break——
裸 break 会把生成器(和它的 HTTP 流)留到 GC,继续为没人读的 token 付费。配套在
MODEL_STREAM 与 DISPATCH 之间加取消边界:没有它,被中途掐断的纯文本流会落进
STOP_CHECK 关成 NO_MORE_ACTIONS——用户打断被伪装成自然结束。已流出的 delta 留在
账本,close 时折叠进 assistant 消息(打断的工作是历史,不是垃圾)。

只决定「下一步做什么」:一切分叉是策略调用、一切能力是通道调用,扩展路线图零改本文件。硬保证:取消只落安全边界且绝不切开配对(合成收口);任何终止路径恰好一个 turn_done(计费链唯一源,finally 兜底);CONTEXT_OVERFLOW→压缩+重试(有进展才重试,防死循环);本文件永不出现轮次/时长上限(铁律 #14)。参数流式:tool_use_start 建 extractor、arg_delta 喂片、tool_use finalize 校齐(流出文本==最终值不变量)。

## 2026-08-23(补)— DRAIN_STEERING 后发 TYPE_STEER_CONSUMED

drain 出注入并 `record_steering` 后,读 `a.steering.take_consumed()`([[steering.py]]),非空则 `yield` 一个
`TYPE_STEER_CONSUMED`(transient ui-track、seq=-1、payload={"ids":[...]},见 [[events.py]])。这是「游标随真消费
前进」的信号源:transport 拦截它、告诉 producer 哪些 steer_inbox 行被真读到(见 [[message_bus_trigger.py]] 补5)。
无 id 时不发。

## 2026-08-23(补2)— 消费信号的 at-most-once 残留(架构标记)

「consumed」= drain 进本 turn 的 ledger、在模型真动它**之前**发信号。相对被关掉的 push-窗口的残留:子进程在这行 flush
后、模型动作前崩溃 → 游标推进却无输出(at-most-once);`record_steering` 抛异常 → id 滞留 inlet 未上报 → 之后重投(非丢失)。
两者都**严格窄于**每 turn 的 push-窗口。trigger 批仍 at-least-once,steer 崩溃时 at-most-once——一个后人该知道、但本 PR 不修的
不对称。loop.py:260 有对应注释。

## 2026-08-24(补3)— steer_consumed 直接 yield,不过 `_log`

`TYPE_STEER_CONSUMED` 现在**直接 yield**、不经 `_log`:它是瞬态控制信号、不是 ledger/NDJSON-truth 行。过 `_log` 会
每次 drain 写一条 seq=-1 进「未来 nexus_events 表镜像」文件,一 turn 几十条会在 `(thread_id,seq)` 键上互撞。直接
yield 让「不是 ledger row」这句话成真。serve_turn 仍能收到(yield 是流,`_log` 只是旁路 sink)。

## 2026-08-23 — WAIT_FOR_INPUT 边界(wait 工具)

DRAIN_STEERING 之后、STOP_CHECK 之前插 WAIT 边界:agent 调 `wait_for_input`(见 [[wait_channel.py]])→ `a.wait.pending`
被置。loop 读清后 `await a.steering.wait_for_input(secs, a.cancel)`:有货→record_steering+continue;超时→注入一条
`NexusPowerPrompts.wait_timed_out(secs)` 通知+continue(取消驱动的空返回则跳过通知,让顶部 cancel 边界中断)。wait 先于
stop=被请求的等待优先于收尾。**消费上报**:wait_for_input 经 `_take_one` 累积 consumed id(与 drain 同),WAIT 边界在
`record_steering(waited)` 后同样 `take_consumed()`+发 `TYPE_STEER_CONSUMED`——steer 进等待中 run 的消息也推 producer 游标。

## 2026-08-24(补)— wait 请求在 DRAIN 前**一次**读清(不跨 step 残留)

`wait_secs = a.wait.pending; a.wait.pending = None` 提到 **DRAIN 之前**执行一次,WAIT 边界只用本地 `wait_secs`。理由:若这步已有 steer 到达,非阻塞 drain 取走它并 `continue`——这**就满足了**等待(agent 求的是"等到输入",输入到了),所以请求绝不能存活到后续 step 再凭 stale intent 阻塞。上提读清是这条保证。回归锁在 `test_a_same_step_message_satisfies_the_wait_and_no_stale_wait_blocks_later`(secs=300,若残留则第二 mute step 阻塞、超 1s wall-clock 变红)。
