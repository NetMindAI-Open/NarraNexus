---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/loop.py
last_verified: 2026-07-31
stub: false
---

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
