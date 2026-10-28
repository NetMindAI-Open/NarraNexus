---
code_file: src/narranexus/platform/repository/browser_policy_repository.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

文档只持久化 owner 的按 origin 脚本权限；"轮次/会话授权与回执由运行时可信 scope 判定"一段已不成立（随审批删除）。

# browser_policy_repository.py — 每个 agent 的浏览器权限文档

## 为什么存在 / 关键决定

`instance_browser_policies` 的 CRUD，一 agent 一行。判定逻辑全在 policy 对象里，
这层只搬 JSON。

持久化记录用户对特权能力的决定，不记录站点访问限制。普通 HTTP(S) 浏览无需授权。
轮次级和会话级授权及消费凭据同样存入 JSON，供 API/MCP 跨进程读取；它们是否
适用于当前操作，由运行时可信 scope 匹配判定，不靠“不持久化”实现过期。

JSON 解析不了的行按缺失处理并记录日志：文件传输能力回退为 ask，任意脚本默认
拒绝，普通网页仍可访问。具体已知字段的合法性由策略对象校验，不在 CRUD 层重复。

## 上下游

BrowserService 与授权处理读取策略；BrowserPolicy 负责序列化和作用域判定。
设置界面的策略写入经服务层处理，该仓储只负责数据库读写。
