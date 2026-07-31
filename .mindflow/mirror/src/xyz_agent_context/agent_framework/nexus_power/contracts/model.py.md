---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/model.py
last_verified: 2026-07-31
stub: false
---

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
