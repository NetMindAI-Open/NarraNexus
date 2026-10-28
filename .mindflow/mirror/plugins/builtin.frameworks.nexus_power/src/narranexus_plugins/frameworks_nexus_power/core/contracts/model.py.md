---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/contracts/model.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（B-03）— `ProviderProfile.thinks_by_default` 与 `ModelRequest.floor_multiplier`

- `ProviderProfile.thinks_by_default: bool = False`：**这个模型**是否在够到文字/工具调用前先把
  output budget 花在隐藏 CoT 上。它是 per-model 事实，由 `_with_model_limits` 从
  [[model_catalog]] 的 `ModelMeta.thinks_by_default` 逐行覆盖，方言行默认一律 `False`。
  刻意与 `thinking_replay` 分开：后者是 reasoning_content 回传**契约**（provider 会不会因为没回传而
  拒掉工具轮），和模型是否真的花 token 思考正交；混用会让只有 deepseek 一个方言行吃到高地板。
  消费方只有 [[profiles]] 的 `output_budget` 地板选择。
- `ModelRequest.floor_multiplier: int = 1`：只放大 `output_budget` 的地板项、只作用于这一个请求。
  loop.py 的截断重试给被重放的那一步置 2，之后复位 1。刻意放在请求上而不是往共享
  `params.extra` 写一个绝对 `max_tokens`：后者会把放大值冻结到 turn 结束。注意地板压过 headroom，
  放大地板在接近满上下文时同样可能把 `input + max_tokens` 推过 wall——这是只放大一步的原因。

## 2026-09-08 — `McpServerSpec` 长出 stdio 形态

`url` 改为可空，新增 `command/args/env` 与 `is_stdio`。此前只有 url，装配把 `{command,args,env}` 读成空 url，插件的
stdio server「连上了空」、工具静默不存在。两种形态互斥，由 [[assembly]] 的 `mcp_spec_from_config` 生成。

## 2026-07-31 — 两个窗口字段不是冗余,且「未实测=相等」必须由构造保证

`context_window` 是我们**选择**管理、压缩阈值据以计算的预算;
`vendor_context_window` 是 `input + max_tokens` 会 400 的**硬墙**。分开是为了让输出
钳制用真实墙,同时不动压缩触发点——把 Opus 的 context_window 直接写成真实的 1M 会让
压缩推迟到 750K,是范围外的行为变更。

**硬墙一律通过 `output_wall` 读,不要直接读 `vendor_context_window`。** 该字段先前是
字面量默认 128_000,只要某行(如 anthropic 行)只写了 context_window=200_000,两者当场
分叉,钳制就拿着一堵**比我们自己管理的窗口还矮**的墙去算剩余空间:免费档默认模型
DeepSeek-V4-Pro 在 120K 输入时被从 8_192 压到 3_904、130K 压到 1_024——正是本次要消灭
的截断形状,只是由我们自己造成。现在它是 `None`=未实测,`output_wall` 回落到
`context_window`,「相等」由构造成立而不是靠两处字面量恰好写一样。

`ModelRequest.input_tokens_estimate`:本次请求输入的预估开销,0=未知(客户端就要满额)。

# contracts/model — 方言是数据不是代码

ProviderProfile 一行数据描述一家 provider(cache 方言/窗口/参数增量能力位),新 provider=加行,未知走保守默认(任何用户模型可跑,铁律 #15)。ModelEventKind 是封闭 Literal(R3):翻译层自己的词汇,构造期校验,拼错当场炸。content_index 对齐分片(pi 纪律:块事件不保证连续)。

## 2026-08-23(补)— STEER_ID_KEY

新增 `STEER_ID_KEY="_steer_id"`:steer producer 可在 ProviderMessage 上盖的私有键,载 steer_inbox 行 id,让 loop 能
报「消费了哪几行」;inlet 在 drain 时剥掉(模型看不到)。定义在 ProviderMessage 契约这一层,producer([[steer_channel.py]])
与 consumer(inlet [[steering.py]])共用同一名字、互不 reach 对方包。见 [[message_bus_trigger.py]] 消费契约。
