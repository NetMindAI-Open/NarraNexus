---
code_file: src/xyz_agent_context/agent_framework/adapters/materializer.py
last_verified: 2026-07-29
stub: false
---
# adapters/materializer.py — 共享的「结构化消息 → CLI 提示文本」物化步骤

## 2026-07-29 — argv 截断逻辑降级为「回落路径的保险」,不要删

claude adapter 现在自己写 CLI 的 resume transcript([[transcript]]),历史正常
情况下走那个文件、**根本不进 argv**。于是 `ARGV_MAX_HISTORY_CHARS` 和按来源淘汰
的驱逐逻辑看起来成了死代码。

**它们不是。** 写 transcript 是 fail-open 的:写不进去(配置目录只读、磁盘满)
就回落到"历史折进 argv 提示词",与改动前完全一致。这几个上限正是防止那条回落
路径撑爆 `MAX_ARG_STRLEN` 的东西。

之所以专门记一条:原 plan(`2026-07-29-synthetic-transcript`)把"整块删除截断
逻辑"列为 T4 的收益,理由是"它存在的唯一原因就是历史要塞进 argv"。这个推理少了
一步——**收益到手不等于代码可删**。而且它要重新引入的故障只在罕见路径上出现,
删掉之后短期看不出任何问题。plan 的 T4 节已改写记录此事。

`CHAT_HISTORY_TIMELINE_PREAMBLE`（原本在 [[prompts.py]]，由
[[context_runtime.py]] 直接拼进 system prompt）搬到本文件。原因是它描述的是
history 区块，而**只有 materializer 知道这个区块还在不在**：预算被挤爆时行被
驱逐，指南却已经焊死在 system prompt 里，模型于是被告知"下面是你们最近的对话，
每行前缀 [time · topic · nar_id]"，然后什么都没有——这是在诱导模型凭空回忆。
prod 2026-07-29 agent_94360f6c4b98：10 轮里 7 轮 0/30 行存活。

现在两条路径都走 `_history_block()`（指南 + 标题 + 正文 + footer 一个整体），
`kept` 为空但原本有行时改发 `_history_omitted_notice(n)`：说明有历史、被预算挤掉、
不要猜、去用 narrative / memory 工具捞。**沉默不是中性的**——没有标记时模型会把这
轮当全新对话，自信地把缺失的上下文编出来。

预算数学是净中性的：`overhead` 涨了指南的长度，但 system prompt 少了同样多。

## 2026-07-28 — flatten_for_argv 拆成两阶段（resume 化 R2/R3）

claude 策略拆为 `split_for_argv(messages) -> (base_system_prompt,
history_entries, this_turn_user_message)` + `assemble_argv_prompt(base,
entries, *caps) -> str`；`flatten_for_argv` 保留为两者的平凡组合（唯一
其他调用方 grep 过：没有——codex 走 flatten_for_file）。动机：resume 轮
要用**空历史**组装（历史在 CLI session 文件里），而陈旧句柄的同轮冷启
动重试要用**保留的同一批 entries** 重组——pop 变异只许发生一次，所以
split 阶段持有它（load-bearing：step_3 fallback 之后读同一个 list）。
组合路径与单阶段函数字节级等价（test_materializer.py 新增 byte-identity
测试，含驱逐/双上限场景）；`assemble_argv_prompt(base, [])` 不追加
history 头尾，双上限仍对裸 prompt 生效（belt-and-braces）。

## 为什么存在

两个 CLI driver 都在门口把 context_runtime 给的结构化 role messages 压
平成「system prompt 文本 + 单条 user 输入」（CLI 只收这两样）。此前是
两份平行私有实现（claude 内联在 agent_loop 里；codex 在 cli_sdk 的
`_build_system_prompt_and_user_msg`）。本模块把两种策略并排收进一个显
式共享接缝：

- `flatten_for_argv`（claude 策略）：argv 承载 → 字符+字节双上限
  （Linux MAX_ARG_STRLEN）、source-aware 驱逐、截断标注；**会 pop 调用
  方列表的最后一条**——load-bearing quirk：step_3 的 fallback 之后读的
  是同一个 list。
- `flatten_for_file`（codex 策略）：文件承载（instructions.md）→ 无字
  节上限、宽预算、同款驱逐；操作副本不变异调用方。

## 刻意不合并成一个函数

两者可观测输出不同（标注文案、上限、变异语义），与历史行为的字节级等
价才是本次上提的契约。自研 loop（自己投影上下文的 driver）根本不调本
模块——这正是 driver 契约「签名一致、消费深度自由」的一半。

## 坑

- 金样测试 `tests/agent_framework/test_materializer.py` 钉住 header/
  footer 字节、驱逐次序（先最旧非 chat 再最旧 chat）、UTF-8 边界截断、
  变异/拷贝语义。改任何输出细节先过金样。
- `_source` 由 context_runtime.build_input_for_framework 从行的
  meta_data.working_source 写入，未知默认 "chat"。
