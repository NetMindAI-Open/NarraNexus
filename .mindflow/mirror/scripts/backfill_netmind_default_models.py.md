---
code_file: scripts/backfill_netmind_default_models.py
last_verified: 2026-04-29
stub: false
---

# backfill_netmind_default_models.py

## 为什么存在

`model_catalog.py` 里的 `_DEFAULT_MODELS[(source, protocol)]` 只在
**新建** provider 时被读一次——把当前默认列表写进 `user_providers.models`
JSON 列。新增一个 model 之后，老用户的 row 不会自动跟上，他们打开 Settings
看到的还是创建当时的快照。

这个脚本就是为了堵这条缝：把当前 catalog 默认列表里有、但某个老 row 还没有
的 model **追加**到 `models` 数组末尾，幂等可重跑。

## 上下游关系

读：`xyz_agent_context.agent_framework.model_catalog.get_default_models()`
   —— 当前默认 model 列表的唯一权威来源。
读+写：`user_providers` 表（SQLite / MySQL 任一，靠 `db_factory` 自动选）。

不动：
- `~/.nexusagent/llm_config.json`（那是 dev 用的本地 provider 注册文件，不
  是用户级 provider 表）。
- 任何 agent / model 选择字段——脚本只动 `models` 列表本身。

## 设计决策

**幂等**：每个 model 用 `m not in existing` 判断；已经包含的不会被改顺序，
也不会重复追加。可以放心重跑。

**追加到末尾，不重排**：保留用户原有顺序（用户可能手动调过排序，比如把
最常用的 model 放最前）。新 model 都进列表末尾，代价是默认列表的"语义
顺序"和老 row 里看到的顺序可能不一致——但不影响功能。

**双协议同时跑**：`_PROTOCOLS = ("openai", "anthropic")`。NetMind 这两个
协议各自独立的 default list，单次运行覆盖两边。

**`--dry-run` flag**：写之前先预览。脚本对真实数据库的修改是不可逆的
（除非自己有备份），所以默认写入也无所谓——但 dry-run 让 CI 或代码 review
能在不动数据的情况下看到 diff。

## Gotcha / 边界情况

- 脚本硬编码了 `source="netmind"`。如果以后要给 yunwu / openrouter 做类似
  事情，**复制这个文件**改名 + 改 source，**不要**让单个脚本支持多 source
  ——那会让运维场景"是否包含这个 source"变成隐藏开关。
- `models` 列在 schema 里是 `TEXT`（JSON 序列化）。脚本用 `json.dumps` 写回
  并保留 `ensure_ascii=False`，避免 Qwen 这种带特殊字符的 model 名被
  unicode-escape。
- 如果某行 `models` JSON 解析失败（手动改坏过），脚本会 `[SKIP]` 该行而
  不是崩溃——不要无脑覆盖用户可能精心配置过的内容。

## 何时跑

`model_catalog.py` 改了 `_DEFAULT_MODELS[("netmind", ...)]` 之后，**同一个
PR 里**跑一次（先 dry-run，再 apply）。提交时把 dry-run 输出和实际 update
计数贴到 commit message 里，留一份审计痕迹。

## 新人易踩的坑

- 跑这个脚本**不需要重起后端**——它直接连 DB。但是**如果同时改了
  catalog**，后端进程要重起，因为 `_KNOWN_MODELS` 缓存是 import 期初始化
  的，DB 里的 model_id 即使更新了，进程内的 metadata 还是旧的。
- `DATABASE_URL` 环境变量必须指向真实数据库；本地默认 `sqlite:///~/.narranexus/nexus.db`。如果你在容器或云上跑，记得换。
