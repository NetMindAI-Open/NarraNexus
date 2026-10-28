---
code_file: tests/nexus_power/test_modeling.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-10（B-03）— 思考地板、clamp 顺序、`floor_multiplier`

`thinks_by_default=True` 得 8_192 地板（该 profile 故意设 `thinking_replay="strip"`，证明地板不看
那个字段）；非思考 profile 仍是 1_024（负例）；地板不超过模型自己的 ceiling（DeepSeek-V3 7_200），
即钉住 `min(ceiling, max(floor, headroom))` 的顺序；`floor_multiplier` 只放大地板、headroom 充足时
不起作用、ceiling==地板时翻倍不增长；catalog 覆盖按模型诚实取值（V4-Pro/Flash、o3/o4-mini 为 True，
V3、gpt-5.5、未知模型为 False）。

# tests/modeling — 方言解析/断点/chunk 翻译/裁剪

假 chunk 流验证事件切分与 usage 双词汇换算;路由前缀按协议且无条件前置(平台 id 可自带路由名,豁免=裸名 404);裁剪保尾。

## 2026-07-31 — 本文件现在承载「输出上限的事实源」这条不变量

除方言解析/断点/chunk 翻译/裁剪外,这里还钉住:上限与窗口来自
`providers/model_catalog`(不是框架内本地表)、协议永远不授予厂商上限、
**抬高上限需同时有实测 window 而压低不需要**、以及钳制(`output_budget`)的边界行为。
这批断言含具体数字(115_200 / 57_600 / 8_192),catalog 改数时会一起红——那是有意的,
它们就是防止两处数字悄悄分叉的哨兵。

## 2026-09-11 — 自填 id 归一化与 `requested_max_tokens`

`deepseek-v4-pro` / `DeepSeek-V4-Pro` / `netmind/deepseek-ai/DeepSeek-V4-Pro` 均得 `thinks_by_default=True` 与
8_192 地板；未知名、非思考模型的自填拼法、近似名仍为 False/None；同名行事实不一致时 `get_model_name_match` 歧义返回 None，
匹配结果不带 model_id/display_name/context_window 且 `get_model_meta` 对它仍为 None；`myorg/Claude-Opus-4-8` 保持方言行的窗口与
ceiling（不借 1M、不抬到 115_200），`myorg/DeepSeek-V3` 的 ceiling 可降到 7_200；`requested_max_tokens` 返回 int，
`None`/非整数不算钉住。
client 实际发送的 `max_tokens` 恰等于 `requested_max_tokens`（未钉 ×1/×2、钉住值在任意乘数下不变）。

## 2026-09-11 — 跨 chunk 代理对

text/thinking 两路代理对切在两个 chunk 时合成正确且每条 delta 可 UTF-8 编码；流末未完成的高位补一条 U+FFFD delta；原始参数分片
切在代理对中间时流式 arg_delta 与最终 args 都合成正确、孤立低位换 U+FFFD；`scrub_json_strings` 干净树返回原对象、脏树只复制变动路径。
回退 joiner 时这些用例变红。
