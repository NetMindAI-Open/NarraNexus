---
code_file: backend/routes/admin/runtime.py
stub: false
last_verified: 2026-10-28
---

## 2026-10-28（PR #419 review I-1）— `/workers` 改用共享 staff 闸

内联的 `is_cloud_mode() and role != "staff"` 换成 `Depends(require_staff_in_cloud)`（见
[[_staff.py]]）；语义不变：local 放行，cloud 非 staff 403（detail 改为 `staff role required`，
与 logs 一致）。测试的 patch 点从 `rt.is_cloud_mode` 挪到 `_staff.is_cloud_mode`；本文件不再
import `is_cloud_mode` / `HTTPException` / `Request`。下面旧条目里"测试 monkeypatch
`rt.is_cloud_mode`"一句已过时。

## 2026-10-28 — `/workers` 云端只给 staff

这条路由没有任何门（同文件 `/status` 有 secret）；`last_error` 带 worker 崩溃的 `repr(e)`，
任何登录用户可读。本地/桌面模式没有角色、System 页要用，照旧放行；cloud 模式
`request.state.role != "staff"` → 403。模式判定用公开的
`narranexus.platform.utils.deployment_mode.is_cloud_mode`（不引 `backend.auth` 私有的
`_is_cloud_mode`）；测试 monkeypatch `rt.is_cloud_mode`。

## 2026-10-28 — secret 改为 `Depends(admin_secret_dependency)`

见 [[_admin_secret]]：依赖项先于 body 校验执行，未认证请求一律 403，不再 422 泄露字段名。
行为对持有 secret 的调用方不变。

## 2026-08-21 — /status 新增第四段 `executor_reaper`

空闲回收器的 L2 出口。回答 `audit_counts` 回答不了的那个问题：**回收器整个
停摆，和"本来就没人该回收"，在外面看是同一个现象**（都是 reap 0 行、
`cull_skipped_busy` 恒 0）。见 [[executor_reaper.py]] 2026-08-21 条目。

**为什么是并列的第四段，而不是塞进 `admission.snapshot()`**：那份快照是
admission 控制器自己的状态，且 docstring 明说是**不持锁**的 best-effort 读；
回收器的账是另一个对象的、按轮更新的诊断，混进去会让两种不同保证的数字看起来
是一回事。

**这一段与前三段的容错性质不同**：`reaper_status()` 读的是
`executor_reaper` 的模块级 dict（per-process，纯内存），**不可能抛**，所以
handler 里没有、也不需要 try/except —— 前三段各自要兜的是 DB / broker HTTP。
这也是它为什么能在 reaper 从没跑过时诚实地报 `running: false`，而不是编一组零。

**要告警的字段**：`stale`（3 个 interval 没跑完一轮 —— 卡死的 reaper 会把上一次
好轮的数字永久冻在那儿）、`task_error`（后台 task 死了；否则只有一行会被日志
轮转吃掉的 error）、`veto_installed: false`（该进程正在按 2026-07-31 事故前的
逻辑回收）、`blind_passes`（连续多少轮判不出活性）。

`503 == 功能关闭` 那条语义**没动**，deploy 侧 watcher 不受影响。

## 2026-08-13 — admin-secret 校验改用共享 helper

原内联的 `_require_admin_secret` 已抽到共享私有模块 [[_admin_secret.py]]
（`require_admin_secret`），与 migration / suspend 同用一份。**503（未配置）/
403（缺失或错误）状态码语义严格不变**——部署侧告警 watcher 依赖 503 == 「功能关闭」。
比较改为 `hmac.compare_digest`（常量时间）。本文件不再直接 import `settings`
（仅为测试经 `mod.settings` 覆盖 secret 而再导出一份 `# noqa: F401`；helper 读同一
settings 单例）。

## 2026-07-22 — added GET /api/admin/runtime/workers (Workers card liveness)

Second read-only L2 endpoint in this module. Reads the latest
`worker_supervisor` heartbeat row from `service_audit` (via
`ServiceAuditRepository.last_heartbeat`, falling back to the `started` row on a
cold boot) and returns a per-worker snapshot `{available, heartbeat_age_seconds,
workers:[{name,state,restart_count,last_error}]}`. Feeds the desktop System
page's single "Workers" card ([[ServiceCard.tsx]] / [[run_worker_supervisor.py]]):
the four merged workers share one process, so the process dot can read "running"
while a sub-worker crash-loops — this exposes each sub-worker's state +
cumulative restart_count. Same discipline as `/status`: every sub-step guarded,
never 500s (DB blip or absent supervisor → `available:false`). Auth:
`/api/admin/*` needs a JWT in cloud but is bypassed in desktop local mode, which
is exactly (and only) where the Workers card renders.

## Why it exists

`GET /api/admin/runtime/status` — 只读 L2 可观测端点,给 executor 调度/资源系统
一个"分钟级发现问题"的窗口(scheduling-resource 设计 §9)。

四段拼装,每段独立容错,**任何单段失败都不让端点 500**:
- `admission`: `get_admission_controller().snapshot()`(活跃用户/loop、各 cap、
  排队深度、free_mem vs 内存阀)。
- `executors`: 经 broker `GET /executors` 取活容器列表;无 `BROKER_URL` 或 broker
  不可达 → `[]`(handler 层 try/except 兜底,`_get_executor_list` 可抛)。
- `executor_reaper`: 空闲回收器上一轮的结果 + 新鲜度(2026-08-21 条目)。纯内存
  读,是四段里唯一不需要 try/except 的一段。
- `audit_counts`: `ExecutorAuditRepository.counts_since(近1h)`,看 OOM/cull/
  orphan-reap 速率。

注入接缝:`get_db_client`(db_factory)、`get_admission_controller` 都是模块级名,
测试可 monkeypatch。本轮未加鉴权(管理端点,部署侧应在网关/反代限制访问 —— 待办)。

## 2026-07-30 — X-Admin-Secret 自凭据（watcher 401 修复）

deploy 仓 alert watcher 是无 JWT 的机器客户端；端点照 migrate-identity 先例
改为 auth 中间件放行 + handler 内 `X-Admin-Secret`（`settings.admin_secret_key`）
校验。未配置 secret = 503 拒绝（配置缺失不是敞门）。历史：watcher 设计时
（2026-06-18）此端点无鉴权，后来全局 JWT 上线把它 401 掉，而 watcher 一直没
部署所以静默失联了一个多月。
