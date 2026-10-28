---
code_file: tests/backend/test_agents_cost_route.py
last_verified: 2026-08-17
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
命名空间上才生效)。**token 数字**用的就是活案例的真实数字,断言值即当时
`sqlite3` 手查的结果。

**时间戳不是**,而且必须不是(2026-08-17)。原来 `created_at` 写死成
`"2026-07-30 08:00:00"`——那是活案例当天的真实时刻,但路由的窗口是
`days=7` 从**现在**往回算,于是这三条测试写完 7 天后必红,并且在 CI 不跑
pytest 的那段时间里一直红着没人知道。现在锚在**昨天 UTC 正午**
(`_ANCHOR`):永在窗口内、永不落到未来、离两侧午夜都够远。

两行必须是**不同时刻**(`_ANCHOR` 与 `_ANCHOR + 1h`),这点容易被"简化成一个
常量"顺手抹掉:路由分日桶用的是 `created_at[:10]` 字符串切片而不是日期解析,
所以只有两行带着不同时间戳,才证明得了"不同时刻收进同一个日桶";共用一个锚点
的版本对着"拿完整时间戳当 key"的回归照样是绿的。

## 坑

缓存两列是 NOT NULL DEFAULT 0(auto_migrate 加列时旧行已回填 0),所以
"legacy 行"的正确模拟是 insert 时**不带**这两个键,而不是显式塞 NULL——
塞 NULL 会直接违反约束。
