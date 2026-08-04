---
code_file: tests/backend/test_agents_cost_route.py
last_verified: 2026-07-30
stub: false
---

# test_agents_cost_route.py — /costs 聚合的缓存桶回归测试

## 为什么存在

2026-07-30 的活案例:agent_39b2b72b823b 的 popover 显示"输入 213",而账本
里该周输入侧真实是 ~1.2M token——`/costs` 路由只读 input+output 两列,把
`cache_read_input_tokens` / `cache_creation_input_tokens`(缓存热时占输入侧
>99%)整个丢掉,还让 helper 行显示得比主 loop 大。这些测试钉死:三桶必须
全部出现在 total / by_model / daily / records 每一层。

## 测试形态

沿用 [[test_event_log_meta.py]] 的 harness:内存 SQLite + auto_migrate +
TestClient,monkeypatch 模块内的 `get_db_client` 和
`resolve_current_user_id`(cost.py 是 from-import,补丁打在 cost 模块
命名空间上才生效)。种子数据用的就是活案例的真实数字,断言值即当时
`sqlite3` 手查的结果。

## 坑

缓存两列是 NOT NULL DEFAULT 0(auto_migrate 加列时旧行已回填 0),所以
"legacy 行"的正确模拟是 insert 时**不带**这两个键,而不是显式塞 NULL——
塞 NULL 会直接违反约束。
