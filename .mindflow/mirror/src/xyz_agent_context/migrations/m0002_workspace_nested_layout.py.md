---
code_file: src/xyz_agent_context/migrations/m0002_workspace_nested_layout.py
stub: false
last_verified: 2026-06-18
---

## 为什么存在

把"flat workspace `{agent_id}_{user_id}` → nested `{user_id}/{agent_id}`"这步
注册进版本化迁移 runner(`migrations/__init__` 的 REGISTRY,id `0002`),让它**每次
启动自动跑一次**(cloud / `bash run.sh` / DMG 同走 backend.main lifespan)。这样
自托管的开源用户从任何旧版本升级,都会被 runner 按 ledger 一级级补到最新,**不靠
人手迁移**——这是 per-user executor 隔离能正确挂到已有数据的前提(broker 按
`{user_id}` 挂子目录,flat 布局下会挂空)。

## 关键点
- 真正的搬移逻辑在 `utils.workspace_paths.migrate_flat_to_nested`(幂等、非破坏:
  目标已存在→冲突留原地;owner 不在 known users→不猜、留原地)。本模块只是把它
  接进 runner + 从 DB 读 known user_ids 消歧 `_user_` 中缀。
- **鲁棒性(每次启动都跑,含全新库)**:`users` 表不存在→known 空集(不崩 runner);
  `base_working_path` 目录不存在→直接 no-op。失败不写 ledger、下次重试(runner 语义)。
- CLI `scripts/migrate_workspace_layout.py`(dry-run 预览 / 显式 base)共用同一函数,
  保留作运维调试。
- APPEND-ONLY:别改 id / 别重排(已写进用户 ledger)。
