---
code_file: src/xyz_agent_context/utils/invite_code_gen.py
last_verified: 2026-05-14
stub: false
---

# invite_code_gen.py — 邀请码生成器

## 为什么存在

邀请码有两个硬约束，决定了它**不能**用 snowflake / 自增 / 号段这类发号器：
1. **不可猜**——顺序/可枚举的码会让注册上限（Mode B cap）形同虚设
2. **人可输入**——要短、无视觉歧义字符

所以这里是 CSPRNG（`secrets`）从去掉歧义字符（无 `0 1 I L O U`）的 30 字符
字母表里取 8 位，加 `NX-` 前缀。

## 上下游关系

- **被谁用**：`InviteCodeRepository.create` —— 生成码后尝试 insert，靠
  `invite_codes` 表的 `UNIQUE(code)` 约束做权威唯一性保证，碰撞则重试。
- **依赖谁**：仅标准库 `secrets`。

## 设计决策

唯一性**不在这里保证**——这个模块只负责"随机 + 格式 + 可读"。唯一性的
source of truth 是 DB 的 unique 约束。30^8 ≈ 2^39 keyspace，200~10000 量级
的码碰撞概率可忽略，加上 repo 层的 insert-重试，正确性与概率无关。

后续优化空间（见 `drafts/logs/invite_code_2026_05_14.md` §5）：更长 body、
分段校验位、可读性分组——均不在 v1 范围。
