---
code_file: src/narranexus/platform/browser/_browser_impl/policy_store.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 第二轮 review）

写锁改为按 agent **引用计数**（`_AgentWriteLocks.hold`），无人持有或等待即删除，长生命周期进程不再为每个改过权限
的 agent 永久留一把锁。建行移到 CAS 循环之前（`_ensure_row`），不再消耗重试次数；吞掉插入异常只限"别人刚建好"
这一竞态且记 debug 日志，其他失败不会留下行、照常抛出。写入直接存 `policy.to_dict()`，旧版本写下的
grants / receipts / 文件能力等死键在下一次写入时被清掉，而不是永久随行。上文"三层"中的第 1 层据此更新。

# Owner 管理的脚本权限（原子写）

公开视图与修改接口只包含 `full_cdp_access`，verdict 只有 allow / deny；origin 输入拒绝凭据、
路径、query、fragment，避免把针对某路径的请求静默放大成整个 origin。撤销就是显式 deny。

## 写入：进程内排队 + 跨进程 CAS + 有界重试（2026-10-08，PR #410 review C1/I7）

`set_rule` 是对整个 JSON 文档的读-改-写，用三层保证安全：

1. **同进程按 agent 串行**（`_write_lock`：按事件循环分桶、按 agent 引用计数的 asyncio.Lock，用完即删）。
   不排队时所有并发写都读到同一个快照、每轮只有一个 CAS 胜出，N 个并发点击需要 N 轮——
   在真实 MySQL 上实测 6 个并发写会耗尽 5 次上限、把正常并发报成冲突。策略只由 API 进程写
   （MCP host 只读），所以这把锁消除了实际会发生的竞争。
2. **跨进程 CAS**：`UPDATE ... WHERE BINARY agent_id = BINARY %s AND BINARY policy_json = BINARY %s`。
   `BINARY` 让 MySQL 逐字节比较 JSON（而不是按大小写不敏感的排序规则）；SQLite 后端会剥掉该
   关键字。每次写都刷新 `updated_at`，所以重复保存同一条规则也必然改变行，不会被 MySQL 的
   "changed rows" rowcount 误判为失败。
3. **有界重试**：`SET_RULE_ATTEMPTS` 次、退避递增，用尽抛 `PolicyWriteConflict`，路由回 503「重试」。
   绝不在没写进去时报成功，也不空转（原来是 `while True` + `sleep(0)`）。

MySQL 孪生测试 `tests/browser/test_policy_store_mysql.py` 覆盖：首写插入、重复同值、过期快照
被拒、同进程并发、跨进程（绕过锁直接 CAS）并发。

此前这里还会清理匹配的会话授权并给待处理审批写回执——审批子系统删除后这些都不存在了。
