---
code_file: src/narranexus/kernel/deployment.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — `is_cloud_mode()` 的唯一实现（三份合一）

之前 `backend/auth.py::_is_cloud_mode`、`utils/deployment_mode.py`、`settings.is_cloud_mode` 各写一份，
启发式还不一致（auth 多一条 `DB_HOST` 回退，settings 只看 `database_url`）。插件 loader 的
云端 fail-closed（D1）需要**恰好一个**答案，于是收敛到内核，另三处转发。
优先级取三者的并集，且顺序不变：显式 `NARRANEXUS_DEPLOYMENT_MODE` > `DATABASE_URL` 非 sqlite >
`DB_HOST` 非空 > local。唯一的语义变化：`utils.deployment_mode` 与 `settings` 现在也认
`DB_HOST`（只有 `DB_HOST` 没有 `DATABASE_URL` 的部署以前在这两处被判 local）——这是修一致性
不是新功能，云端 `.env.cloud.example` 用的是 `DATABASE_URL`，实际无影响。
函数接受可注入的 `environ` 便于测试矩阵（`tests/nx_kernel/kernel/test_deployment.py` 同一矩阵
跑内核、auth、legacy 模块、Settings 四处）。默认 local 的安全理由（DMG 的 `set_var` 线程不安全）
保留在 auth 的 docstring。
