---
code_file: src/narranexus/platform/repository/team_bulletin_repository.py
last_verified: 2026-08-11
stub: false
---

# team_bulletin_repository — 公告栏的数据访问

## 为什么存在

PRD 的诊断一句话：team 级别不存在「持久 × 共享 × 每轮必然载入」三者交集的状态。
`teams.description/intro_md` 持久且共享但**从不进 prompt**；scrollback 进 prompt 但
只有 20 条、不持久；agent 各自的记忆持久却不共享。公告栏就是补这个空位，这张表是它的落地。

## 两个决定写在这一层，而不是留给调用方

**自动总结是「槽」，不是一类条目。** 每个 team 至多一行 `source='auto_summary'`，原地覆盖，
且 `upsert_summary` 是它唯一的写入口——所以没有调用方能不小心把它变成一个列表。
它每轮都进 prompt：累积会重现公告栏本来要解决的问题（无界常驻文本挤掉对话），
而覆盖让「总结质量差」的损害封顶在一条，不会层层叠加。

**总结不占用条目预算。** `usage()` 排除它。若计入，一段自动的、尽力而为的、可能滞后的文字
就能把用户亲手写的规矩挤出 prompt —— 平台用自己的猜测压过用户。它有独立上限
`BULLETIN_MAX_SUMMARY_CHARS`。

## 注意

- 排序是 **oldest-first**。prompt 里规则是编号的，agent 这轮被告知「规则 2」，
  下轮不该发现规则 2 换了内容。
- `delete_tier` 显式排除总结：总结不属于任何 tier，「清空本任务」不该顺手把它带走。
- 预算**不在**这一层强制。拒绝需要给出解释（「限 500 字，你写了 900」），
  这一层没有地方放那句话——见 [[team_bulletin]]。

相关：[[team_schema]]（常量与模型）、[[schema_registry]]（建表）、
[[team_bulletin]]（预算与 agent 权限）、[[team_summary_worker]]（写总结槽的人）

## 2026-08-11 (review) — `update_content` 现在推进 `updated_at`

db client 不会自动动这一列，所以显式写。留着不动的话，「最后编辑于」报的会是规则**最初写下**的
时间——而那恰好是读者在一条规则让他意外时最想问的东西。
