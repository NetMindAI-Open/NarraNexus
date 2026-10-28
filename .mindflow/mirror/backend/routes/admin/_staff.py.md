---
code_file: backend/routes/admin/_staff.py
last_verified: 2026-10-28
stub: false
---

# admin/_staff.py — 用户会话型 admin 路由的共享 staff 闸门

## 为什么存在

PR #419 review I-1：`admin/` 包里"是不是 staff"有三种写法（logs / quota / runtime），
对"非云端怎么办"的回答互相矛盾。本模块是**唯一一份**"local 放行、cloud 只给 staff"的
判定，与 [[_admin_secret.py]]（自凭证 `X-Admin-Secret` 路由）并列：那边回答"你是不是
运维方/watcher"，这边回答"这个登录用户是不是 staff"。

## 语义（写死在 docstring 里）

- **local / 桌面模式 → 放行**：没有角色体系，唯一的本地用户就是运维者，桌面 System 页
  会调这些路由。
- **cloud 模式 → `request.state.role == "staff"`，否则 403**。cloud 下 role 缺失（`None`
  / `""`）**拒绝**——"是不是 local"只由 `is_cloud_mode()` 决定，绝不由"没有 role"推断。
- 依赖项形式：`dependencies=[Depends(require_staff_in_cloud)]`，在 body 校验之前执行。

## 上下游关系

- **被谁用**：[[runtime.py]] 的 `GET /api/admin/runtime/workers`（`last_error` 带 worker
  异常文本，cloud 只给 staff）。
- **依赖谁**：`narranexus.platform.utils.deployment_mode.is_cloud_mode`（公开函数，不引
  `backend.auth._is_cloud_mode`）。测试 monkeypatch `backend.routes.admin._staff.is_cloud_mode`。

## 刻意没有迁过来的副本（不是遗漏）

- **`admin/quota.py::_require_staff_or_raise`**：非云端回 **503** 而不是放行——quota admin
  端点离开钱包服务没有意义，这是故意的；本 helper 只表达"local 放行"一种策略，迁过去会
  静默改掉 quota 的行为，所以不动。
- **`admin/logs.py::_require_staff`**：用"`role is None` → 当作 local 放行"判定，不看模式。
  与本 helper 不等价（cloud 下若某条路径没填 role，logs 会放行、本 helper 会拒绝），按
  review 约定"语义不同就不迁"，留作 follow-up（迁移会让它更严，需要单独确认 System 页
  日志卡在所有部署形态下都有 role）。
- **`routes/providers.py` / `routes/agents/llm_config.py` 的 `_is_staff`**：返回布尔交给
  policy 函数，不直接 raise，是另一层；follow-up 可统一成共享谓词，不必并进本闸门。

## Gotcha

- **触发**：新 staff 路由照抄 logs 的 `role is None → 放行` → **症状**：cloud 下任何未经
  auth_middleware 填 role 的路径（例如进了 `AUTH_EXEMPT_PATHS`）对所有人开放 → **根因**：
  把"缺 role"当成"local 模式"。用本 helper，模式只由 `is_cloud_mode()` 决定。
