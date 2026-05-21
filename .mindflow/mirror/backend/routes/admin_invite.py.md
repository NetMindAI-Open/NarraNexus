---
code_file: backend/routes/admin_invite.py
last_verified: 2026-05-15
stub: false
---

## 2026-05-15 — promote 不再自动发邮件

`utils.mailer` 已被删除(SMTP 责任移到 `narranexus-website`)。`promote` 不再
调 `_send_code_email`,改为**返回 code + email** 给调用方,运营按需手动通知。
后续可由 website 加一个"resend invite by code"的内部 endpoint 来恢复自动重发,
v1 不做。

# admin_invite.py — Staff 专用邀请码管理

## 为什么存在

Mode B 的运营侧:当自动发码到达 cap,新申请进 waitlist;管理员需要能看
全量码、把 waitlist 的码 promote 成 issued、撤销码。

## 上下游关系

- **被谁用**:`backend/main.py` 挂载(自带 `/api/admin/invite` 前缀);
  云端运营/staff 调用。
- **依赖谁**:`InviteCodeRepository`(list_all / promote / revoke /
  get_by_code / count_active)、`backend.config.settings`。
  **不再依赖** `mailer` 或 `invite._send_code_email`(2026-05-15 架构 pivot)。

## 设计决策

- **鉴权 = staff JWT**,`_require_staff_or_raise` 与 `admin_quota.py` 完全
  一致:local 模式 503、无 user 401、非 staff 403。
- **前缀 `/api/admin/invite`**:落在 `auth.py::QUOTA_BYPASS_PREFIXES` 的
  `/api/admin` 下——JWT 照验,但跳过 quota resolver(运营操作不花 quota)。
- **promote 返回 code + email**:waitlisted → issued 后,把码和邮箱返回给
  调用方;由调用方/运营人员决定怎么送达。NarraNexus 没有 SMTP 凭证了。
- **revoke 不碰 used 码**:账号已建,撤码无意义;`repo.revoke` 只对
  issued/waitlisted 生效,否则返回 404。

## Gotcha

- `_code_to_dict` 仍输出 `email_sent` 字段,但在新架构下 NarraNexus 不再写
  它——一直是 0。前端可以隐藏,或保留作为"website 是否回写过"的位。
