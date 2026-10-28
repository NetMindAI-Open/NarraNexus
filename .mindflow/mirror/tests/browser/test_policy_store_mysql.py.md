---
code_file: tests/browser/test_policy_store_mysql.py
last_verified: 2026-10-08
stub: false
---

# 脚本权限 CAS 的 MySQL 孪生测试

`PolicyStore.set_rule` 是浏览器唯一的手写 SQL，而 SQLite 后端会剥掉 `BINARY`，从未按原样执行过它。
只有真实 MySQL 才有的三件事在这里验证：`BINARY` 比较 MEDIUMTEXT 的 JSON、aiomysql rowcount
（changed vs matched rows）作为"写进去了吗"的信号、同一行上的真实并发写。

这个文件第一次运行就抓到一个真 bug：6 个并发写耗尽 5 次重试上限，把正常并发报成冲突——由此引出
policy_store 的进程内按 agent 串行。跨进程场景用多个独立连接直接调用 `_swap_rule`（绕过进程内锁）。
gate 环境变量统一用 `tests.mysql_dialect`（`tests/test_mysql_gate_single_source.py` 校验）。
