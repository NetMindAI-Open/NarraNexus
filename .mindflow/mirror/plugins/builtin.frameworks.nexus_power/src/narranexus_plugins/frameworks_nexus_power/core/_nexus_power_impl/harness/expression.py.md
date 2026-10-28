---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/harness/expression.py
last_verified: 2026-08-31
stub: false
---

## 2026-08-31（二）— 「delivered to no one」加上限定

上一版把这句写成了无条件断言，而**同一个 PR 亲手做出了反例**：
[[../prompts/library]] 在 `default_reply_tool` 为空时告诉模型「本轮明文就是
产物」。文件头是下一个接框架的人读的第一段字，让它说反话代价最大。

现在写成「**缺省**不投递」，并点明例外：平台可以故意不给任何表达工具，
**因为它打算自己投递明文**（NarraNexus 的 team patrol 状态行）。同时改掉
「mute」的读法——不是「这个 agent 写的东西出不去」，而是「本轮没有可用来说话
的工具」。

**真正会咬人的场景在下一个调用方身上**：他复用 patrol 的接线（把表达工具撤空），
却按「明文出不去」的假设设计，于是把 agent 以为没对任何人说的斟酌塞进别人的
收件箱。今天只有 patrol 一个调用方，所以这句注释是唯一的守卫。

顺带整段 reflow，消掉上一次插入留下的孤立短行。

## 2026-08-31 — 契约措辞随宪法改口（无行为变化）

文件头原写「text is the agent's private thinking」。宪法改口后改为「working
narration —— 其 owner 可以看，但不送达给任何人」。**契约本身一个字没改**：
明文永远不是回复，出去必须走 expressive tool call。改的只是对「为什么」的
描述，免得读者从 docstring 里学到一个宪法已经不承诺的东西。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

名单从冻结 frozenset 变**有序可增量**(dict-as-ordered-set):`add_tools` 由
CapabilityExpander 在展开时调用(中途到货的 channel 回复工具也算表达);
`names()` 保声明序去重——**首位=本回合默认回复工具**(constitution 例子取它)。
增量只被动态尾部 reminder 消费,稳定前缀装配时冻结,零 cache 代价。

# harness/expression — 独白/表达契约默认实现

ExpressionPolicy 的名单制实现(R5 已策略化):is_expressive/独白盖章/有机回复统计单点收敛,loop/dispatcher/adapter 不散落 if。空名单合法(哑 agent)。
