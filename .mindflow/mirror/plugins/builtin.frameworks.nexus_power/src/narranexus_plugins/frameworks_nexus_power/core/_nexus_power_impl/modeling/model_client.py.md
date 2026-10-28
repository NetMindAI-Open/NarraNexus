---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/modeling/model_client.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-10（B-03）— `max_tokens` 取自 `requested_max_tokens`

`stream_step()` 发送 `extra["max_tokens"] = requested_max_tokens(profile, params.extra, input_tokens_estimate,
floor_multiplier=request.floor_multiplier)`（[[profiles]]）：用户在 `params.extra` 钉的值原样胜出，否则是
`output_budget`。loop.py 的截断重试调同一函数判断「实际发了多少 / 翻倍能否更大」，两处不再各写一份
「钉住优先」的规则。调用方的 `params.extra` 不被修改（先拷贝）。乘数语义见 [[model]]。

## 2026-08-14 — 自家网关名单补 `llm-gateway`

`_OWN_GATEWAY_HOSTS` 加 `llm-gateway`（原为 `litellm`/127.0.0.1/localhost）。2026-08-07 RCE 整改后
云端 executor 落 sandbox 网络，到网关唯一入口是 `http://llm-gateway:4000`（Caddy 白名单前置 → litellm），
`litellm:4000` 云端够不到——漏掉它，依赖 `_is_own_gateway` 的两个头在云端一次都不发。
- **真实价值在平台身份头**：[[api_config]] 的**同名孪生**名单用于 `X-NarraNexus-Identity-Token`，
  deploy `staging`（#20）的 prefill_compat `_enforce_identity` 会验签它（待 `NX_IDENTITY_VERIFY_MODE`
  开启，**默认 off**，档位见 [[api_config]]）；漏 `llm-gateway` = 身份头云端不发。
  两份名单由 `test_gateway_host_lists_stay_in_sync` 强制一致，改网关拓扑/名字两处必须同改。
- **`x-nexus-prefill-retry`（opt-out 头）的当前状态见下方 2026-07-31 段的更正**：补 `llm-gateway`
  只让它在云端**发得出去**，是否生效由网关侧是否读它决定（当前 inert）。
- **⚠ 别据此删 loop.py 的 prefill 自救路径**：`PREFILL_REJECTED` → `_continuation_turn` 修复与
  `_ends_with_assistant` 二次判定（`loop.py`，以符号名为锚）**与网关是否注入无关**——直连/BYOK 后端真返
  400 时靠它自救；即便走自家网关，被 `_has_tool_use` 豁免的尾部（tool_use 尾部网关一个字都不加）同样得不到
  网关兜底（网关注入的那句续写客户端根本看不见、无从去重）；即便 opt-out 头哪天生效也不能删。

## 2026-08-03 — `_price_row` 删除，价格解析下沉到 [[model_pricing]]

`price_usage` 现在只做「桶 × 单价」的加法，单价从 `price_for(model)` 来。

删掉的 `_price_row` 与 `model_pricing` 是**两份实现同一条规则**（input/output 单价、
cache 读写无公布价回退 input 价、未知返回 None 逐条一致），但 id 解析不同：这份剥 route
前缀 + 大小写不敏感，那份两者都不做。于是**同一个 model id 在两个账本里一个有价一个记 $0**
—— `minimax/minimax-m2.5`（1416 次调用）就是这么在 helper 侧被记成免费的。

合并方向是**保留这份的宽松解析**（它是对的那一半），搬进 `model_pricing`。本文件因此不再
出现 `import litellm`，那条「必须走座位」的注释随 `_price_row` 一起删除 —— 座位约束现在由
`model_pricing` 侧的静态测试守着。

## 2026-07-31 — 截断从字节判,不从 stop_reason 判

_parse_args 三返回值 (args, parse_error, truncated)。truncated 由 _is_cut_short
从收到的字节判定,**不看上游 stop_reason**:NetMind 免费档网关对被自己输出上限
切断的调用报 stop_reason="tool_use"(2026-07-31 实测复现,max_tokens=2000、
output_tokens=2000、参数被切成 `{"path": "game.html"`)。信了它就会把「太长了」
说成「JSON 写坏了」,两种自救方向正好相反。

判据四条,因为切口能落在四种位置。两条显然:msg 以 "Unterminated string" 开头
(切在值内部),或 exc.pos >= len(raw)(切在 token 之间)。另两条**看起来像损坏其实
不是**,靠「报在缓冲区中间就是损坏」会漏判——而它们恰好会触发本次要消灭的转义红鲱鱼:

- 裸字面量切一半:`{"a": tru` → Expecting value @6/9,尾巴是 true/false/null 的真前缀
- `\uXXXX` 转义切一半:`{"a": "x\u00` → Invalid \uXXXX escape,尾巴不足 5 字符

真损坏仍落在四条之外:`trX` 不是任何字面量前缀,`\uZZZZ` 尾巴是全长的。

## 2026-07-31 — 向网关声明「prefill 我自己重试」

> ⚠ **2026-08-14 更正（取代本段下面关于"已退出这层保护"的结论）**：本段写的是**设计意图**。网关侧
> 读 `x-nexus-prefill-retry` 的逻辑写在**未合入**的 deploy 分支 `fix/gateway-prefill-opt-out`（`5df4f22`），
> 当前部署的 `prefill_compat.py` **不读它** → 这层 opt-out 在生产上 **inert**：网关照旧对**纯文本尾部
> assistant**（`_has_tool_use` 命中的 tool_use 尾部**豁免**、非"所有以 assistant 结尾"）补续写。要真正
> 退出这层保护需合并该 deploy 分支。见上方 2026-08-14 段。

extra_headers 里恒带 PREFILL_SELF_HANDLED_HEADER。网关 hook 默认给纯文本「以
assistant 结尾」的对话补一句续写 user 消息以躲开某些后端的 400,代价是对本来能
接受 prefill 的后端也白付一次重复措辞。本循环能自己重试(见 loop 的
PREFILL_REJECTED),所以**意图**退出这层保护,只在真撞 400 时才付代价;claude CLI 没有
重试路径,继续被网关兜着。改名是 deploy 仓 lockstep 改动
(stacks/narranexus-app/litellm/prefill_compat.py)——但**对端今天只存在于未合分支**(见上方横幅),
所以两边不同步是**安全的**:认不出的 header 只会让 hook 继续改写,即今天的行为。

只发给自家网关(`_is_own_gateway` 按 base_url 主机名判):这是和我们自己 proxy 的私下
约定,直连 OpenAI/DeepSeek 时带上它零收益,只是把内部词汇泄给外部厂商。

另:`max_tokens` 不再是常数,改由 `output_budget(profile, request.input_tokens_estimate)`
给出——厂商上限与「窗口剩余」的下确界。见 profiles mirror。

## 2026-07-30 — _parse_args 不再静默 {"_raw": ...}

坏参数 JSON 显式随 tool_use payload 的 parse_error 字段出去((args, err) 双返回),
args 恒为 {}。旧的 _raw 兜底让截断调用带着丢失的字段继续执行,下游报错全部
指向错误方向。解析失败信息带 char 位置与总长,供模型判断截断点。

# modeling/model_client — litellm chunk→ModelEvent 翻译层

LitellmClient 管连接透传,本类管语义:cache_plan 按方言注入 cache_control、usage 双词汇归一(OpenAI cached_tokens 含在 prompt 内→换算为 exclusive)、tool_use_start 名字先到(E3 时序安全)。重大坑:自定义 base_url 时路由必须**显式写死**——模型 id 自带斜杠(minimax/minimax-m2.5、deepseek-ai/DeepSeek-V3)会被 litellm 误当 provider 前缀。但写死的是 provider 决定的**协议**,不是「有 base_url 就 anthropic」:后者让 openai 协议的卡回 AnthropicException(实测)。且路由前缀**无条件前置、不做 startswith 豁免**——平台 id 本身可以以路由名开头(NetMind 的 anthropic/claude-sonnet-5、openai/gpt-5.4),litellm 恒吃掉第一段,豁免会把裸名发上游,NetMind 无裸名 alias 直接 404 unknown model(2026-07-30 dev 事故);双前缀外层被 litellm 消费,完整平台 id 才能上线。tool 方言重写同理,只在 anthropic 路由上做(绕开严格网关对 type:"custom" 的 serde 拒绝),openai 端原样透传。

## 2026-09-11 — provider 文本全入口源头 scrub surrogate

`stream_step` 的三个 provider 文本入口都在此处变成严格可 UTF-8 编码，subprocess 与进程内 executor 两种运行形态由同一处保证，
不依赖出口层兜底：
- `text_delta`（`delta.content`）与 `thinking_delta`（`delta.reasoning_content`）各一个 `SurrogateJoiner`（来自 [[arg_stream.py]]）：
  provider 按 UTF-16 码元切 chunk 时，结尾的高位先挂起、与下一 chunk 开头的低位合成；流结束仍未配对的一半以 U+FFFD 补发一条 delta。
- `arg_delta` 每个 call 一个 joiner，只作用于流式展示；`raw_arguments` 保留原始线上文本用于解析。
- `_parse_args` 解析成功后 `scrub_json_strings`：`json.loads` 保留的孤立 `\uD8XX` 转义或跨 chunk 的原始半边，
  在最终参数里合成或换 U+FFFD，ledger、事件存储、MCP 调用体都拿到干净值。
