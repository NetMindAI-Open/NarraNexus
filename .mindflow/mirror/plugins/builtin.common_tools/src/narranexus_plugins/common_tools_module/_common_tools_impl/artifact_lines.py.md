---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/artifact_lines.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — 空 user_id 不再传给 `agent_workspace_relpath`

[[workspace_paths]] 自 2026-10-28 起拒绝空 id。这里之前用 `user_id or ''` 拼"本工作区前缀"，
空 user 时得到的前缀本来就不可能匹配任何 artifact，所以改成没有 user 就不生成前缀——
行为相同（全部按绝对路径渲染），只是不再触发 ValueError。

# artifact_lines.py — agent 面清单行的唯一渲染方

状态块(每轮)与 list_artifacts 工具(按需)必须显示**同一种行**——否则同一份库存
agent 要学两种方言。路径规则从状态块原样迁入:自己工作区内=短相对路径(文件工具
吃的形式);队友/团队共享=绝对路径(裸 base 相对路径会被禁闭层 rebase 到读者自己
的工作区,指向不存在的文件);URL tab 指向 content.md 快照。两个调用方,谁改格式
都在这一处改。

## 2026-08-20 — 返回 (artifact_id, line) 对(#334 I11)

标记配对显式化:id 随行走,位置 zip 的静默错位整类消灭。两个消费方
(状态块/list_artifacts)同步改。
