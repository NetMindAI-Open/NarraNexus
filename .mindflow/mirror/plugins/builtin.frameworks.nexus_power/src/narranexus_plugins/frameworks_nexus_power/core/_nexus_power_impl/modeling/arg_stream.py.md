---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/modeling/arg_stream.py
last_verified: 2026-09-11
stub: false
---
# modeling/arg_stream — 流式参数抽取(真 tokenizer)

「回复也流式」的技术核心:容器栈+键值位置+跨片段转义/\uXXXX 处理,只流根层声明字段(嵌套同名不泄漏;数组元素不腐蚀键追踪——曾修的两个坑)。finalize 校齐保证流出==最终值(锁定不变量)。pi 三纪律按构造满足。

## 2026-09-11 — surrogate 对合并（prod 事故修复）

MiniMax 这类 provider 按 ensure_ascii 流式吐工具参数，emoji 以 UTF-16 代理对 `\ud83d\udc4b` 到达。旧实现逐个 `chr()` 每个
`\uXXXX`，产出两个孤立 surrogate，下游任何严格 UTF-8 编码（事件日志落盘）都抛 `surrogates not allowed`，NarraMessenger 回合
中断、回复没发。现在高位 surrogate 先挂起（`_pending_high`，可跨任意 fragment），遇到低位即合成星平面字符；无配对的一半
（孤立高位后跟非低位、孤立低位）一律输出 U+FFFD。新增公开 `scrub_surrogates(text)`：相邻两半合成、孤立一半换 U+FFFD，
无 surrogate 时原样返回。finalize 先对最终值 scrub 再校齐，所以「流出 == 最终值」不变量对 scrub 后的值仍成立。

补充（同日预审后）：`scrub_surrogates` 走快速路径（`isascii()` 或预编译正则未命中即原样返回，1MB 非 ASCII 约 3ms）；
新增 `scrub_json_strings(value)` 对解码后 JSON 的全部字符串（含键）递归 scrub，供 [[model_client.py]] 的 `_parse_args` 使用。

补充（同日 review 后）：新增 `SurrogateJoiner`（`feed` 挂起结尾高位并与下一段合并，`flush` 把未完成的一半换 U+FFFD），
供 [[model_client.py]] 的 text/thinking/arg 三个流使用；`scrub_json_strings` 对无 surrogate 的子树返回原对象不复制
（依赖 `scrub_surrogates` 干净时原样返回这一前提）；`_REPLACEMENT`/`_SURROGATE` 常量上移到首个使用者之前。
