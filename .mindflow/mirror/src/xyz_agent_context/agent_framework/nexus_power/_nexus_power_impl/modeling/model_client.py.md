---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/modeling/model_client.py
last_verified: 2026-07-31
stub: false
---

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

extra_headers 里恒带 PREFILL_SELF_HANDLED_HEADER。网关 hook 默认给所有「以
assistant 结尾」的对话补一句续写 user 消息以躲开某些后端的 400,代价是对本来能
接受 prefill 的后端也白付一次重复措辞。本循环能自己重试(见 loop 的
PREFILL_REJECTED),所以退出这层保护,只在真撞 400 时才付代价;claude CLI 没有
重试路径,继续被网关兜着。改名是 deploy 仓 lockstep 改动
(stacks/narranexus-app/litellm/prefill_compat.py),但两边不同步是**安全的**——
认不出的 header 只会让 hook 继续改写,即今天的行为。

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
