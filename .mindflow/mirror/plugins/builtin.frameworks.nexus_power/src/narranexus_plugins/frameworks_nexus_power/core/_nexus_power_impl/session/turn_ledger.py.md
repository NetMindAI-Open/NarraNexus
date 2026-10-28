---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/session/turn_ledger.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

带图的工具结果：provider 视图里 tool 消息 content 为 [text, image_url...]，由 projector 再搬到 user 消息。
事件日志（NDJSON 真相文件、子进程 stdout）只记 `ToolImage.describe()`（mime、base64 长度、sha256），
不记字节——没有任何路径会用日志重建 provider 请求，存字节只会让每张截图多写几 MB。

## 2026-09-08 — 步内 CoT 折进 assistant 消息的 `reasoning_content`

`thinking_delta` 此前只发 ui 轨、不进账本状态；折叠出的 assistant 消息没有 CoT。DeepSeek 思考模式要求
同一轮工具循环里产生 tool_calls 的那段 CoT **原样回传**，否则下一次请求 400「reasoning_content must be
passed back」——NetMind OpenAI 端点 + DeepSeek-V4-Pro 实测每个多步工具回合都死在第 2 步（2026-09-08，本地
插件工场 E2E 抓到）。现在按步累积 `_step_thinking`，`_fold_step_message` 有则写 `reasoning_content`（无则不写键），
`discard_step` 一并丢弃。账本只负责**保留**；发不发给 provider 由投影按 `ProviderProfile.thinking_replay` 决定
（见 [[projector]]、[[profiles]]），所以这里对所有 provider 行为一致。

## 2026-07-30 — tool_use_start 发「名字先行」ui 事件

`record_model_event` 的 `tool_use_start` 从「无账本状态、返回空」改为发一条
ui 轨 `TYPE_TOOL_USE_START`（payload = call_id + tool_name）。参数是流式生成
的，名字远早于调用完成就已知道——不发这条事件，前端只能等 tool_use（参数齐）
才知道在调什么，长参数流上是一段可见的空窗。不进账本状态：真相由随后的
tool_use 落定（同 call_id 覆盖语义在 adapter/前端实现）。

# session/turn_ledger — 回合唯一真相

三不变量构造保证(配对只能经 synthesize 收口/角色交替经步末折叠/seq 唯一分配)。step 文本+调用在 step_done 折叠成单条 assistant 消息(role 交替成立的机制)。compaction 登记 seq→消息替换,投影替换、日志留全史。resume=base 前缀续 seq。
