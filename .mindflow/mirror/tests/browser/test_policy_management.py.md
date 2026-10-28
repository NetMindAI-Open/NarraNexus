---
code_file: tests/browser/test_policy_management.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

新增：并发写之后写锁表为空（不随 agent 累积）；一次写入把旧文档改写为只含当前模型的内容。

## 2026-10-08（PR #410 review）

并发用例改为多个 origin 的并发写全部落地；新增无限竞争时以 `PolicyWriteConflict` 退出（恰好
`SET_RULE_ATTEMPTS` 次）与路由 503 用例。

# 高级脚本管理回归

公开视图只包含 full_cdp_access，显式保存与撤销仍有所有权和输入校验。真实 SQLite 用例验证
并发脚本修改不会丢失独立文件能力的答复。HTTP 明确拒绝 access=allow/ask/deny 和旧撤销请求，
失败不修改原策略；测试不假设其他用例尚未写入任何规则。
