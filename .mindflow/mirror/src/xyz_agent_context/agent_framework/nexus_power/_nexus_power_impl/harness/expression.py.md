---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/harness/expression.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

名单从冻结 frozenset 变**有序可增量**(dict-as-ordered-set):`add_tools` 由
CapabilityExpander 在展开时调用(中途到货的 channel 回复工具也算表达);
`names()` 保声明序去重——**首位=本回合默认回复工具**(constitution 例子取它)。
增量只被动态尾部 reminder 消费,稳定前缀装配时冻结,零 cache 代价。

# harness/expression — 独白/表达契约默认实现

ExpressionPolicy 的名单制实现(R5 已策略化):is_expressive/独白盖章/有机回复统计单点收敛,loop/dispatcher/adapter 不散落 if。空名单合法(哑 agent)。
