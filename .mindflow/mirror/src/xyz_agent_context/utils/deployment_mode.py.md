---
code_file: src/xyz_agent_context/utils/deployment_mode.py
last_verified: 2026-07-13
stub: false
---

# deployment_mode.py — 部署模式 / Power 登录能力的唯一真源

## Why it exists

系统多处要按"云端多租户 vs 本地/桌面"改行为。此前 `_is_cloud_mode()` 逻辑在
provider service 里重复且只看 `DATABASE_URL`。本模块收敛成一个纯 env leaf
（无 DB、同步、易 monkeypatch）。

## 两条正交轴（关键设计，勿混用）

历史上 `is_cloud_mode()` 被过载,同时表达"(A) 多租户云服务器→强制 JWT"和
"(B) NetMind/Power 账号能力可用"。本地双模式登录需求把二者拆开:

- **`is_cloud_mode()` / `get_deployment_mode()`** —— 轴 A,**安全 regime**。
  只决定 `backend/auth.py` 的 JWT 强制。本地保持 `X-User-Id` 信任(OS 用户即
  边界)。**绝不能**把它扩成 Power 登录能力,否则本地 Power 部署会开始对每个
  `/api/*` 要求签名 JWT,击穿本地身份模型。
- **`is_power_login_enabled()`** —— 轴 B 的**部署级**能力:本安装是否允许用
  NetMind(Power)账号登录。= `is_cloud_mode()` OR 本地经
  `NARRANEXUS_ENABLE_POWER_LOGIN` 显式开启。门禁:netmind-login 路由可达性、
  billing `/plans` 公共目录、登录页 Power 入口。
- **`is_power_account(user_id)`** —— 轴 B 的**用户级**,不在本文件(需查库),
  见 [[power_account.py]]。门禁:用户维度 billing + Account 面板。

## 设计决策

- `NARRANEXUS_DEPLOYMENT_MODE`(cloud/local,大小写不敏感,非法值回落 local)
  优先于 `DATABASE_URL` 非 sqlite → cloud 的 legacy 启发式。
- `_local_power_login_opt_in()` 的 truthy 拼写(`1/true/yes`)与
  `backend/auth.py` 的 flag 解析一致,保持所有 env 布尔行为统一。
- 部署契约:云端 `.env` 设 `NARRANEXUS_DEPLOYMENT_MODE=cloud`;本地桌面若要
  Power 登录则设 `NARRANEXUS_ENABLE_POWER_LOGIN=true`(run.sh 与 DMG 都要设,
  铁律 #7)。

