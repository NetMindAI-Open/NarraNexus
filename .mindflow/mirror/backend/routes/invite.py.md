---
code_file: backend/routes/invite.py
last_verified: 2026-05-15
stub: false
---

## 2026-05-15 — 重构成内部 endpoint(架构 pivot)

原来的 `POST /api/invite/request` 是公开 endpoint,自己生成码 + 发邮件。
现在改为 **server-to-server 内部 endpoint** `POST /api/invite/internal/issue`,
理由:把 marketing funnel(申请 + 发邮件)从产品后端剥离到 `narranexus-website`,
让 SMTP 凭证、UX 文案、限流跟着 funnel 走。

- 鉴权:`X-Internal-Secret` header,匹配 env `INTERNAL_INVITE_SECRET`。
  没设 → 503(opted-out);不匹配 → 401。**不**走 JWT(调用方是另一个 server)。
- **不**做 `_is_cloud_mode()` 守卫:`invite_codes` 是张表,本地 SQLite / 云 MySQL
  都能落码。注册侧仍 cloud-only,这是另一层。
- **响应回传 `code`**(server-to-server 信任,website 拿来发邮件,不到浏览器)。
- 限流交给 website 那一边做(它在公共边缘,可以加 Turnstile / per-IP / per-email)。
  这里只校验输入 + 幂等 + cap,假定调用方可信。

# invite.py — 邀请码内部签发

## 为什么存在

云端注册门禁需要"唯一、按邮箱、用后即焚"的邀请码。按"产品 vs marketing
funnel"切分责任:
- website 负责:申请表单 UI、限流、调本 endpoint、用 SMTP 发邮件
- NarraNexus 负责:码的生成 + 落库 + 幂等 + cap + 注册时原子消费 + admin

## 上下游关系

- **被谁用**:`narranexus-website` 的 `app/api/invite/route.ts`(经
  `X-Internal-Secret`)
- **依赖谁**:`InviteCodeRepository`、`backend.config.settings.invite_auto_issue_cap`、
  env `INTERNAL_INVITE_SECRET`。**不再依赖** `utils.mailer`(已删除)。

## 设计决策

- **幂等优先级**:`used > issued > waitlisted > new`。同邮箱已 used → 拒;
  已 issued → 返回同一个码(website 重发邮件);已 waitlisted → 告知。
- **Cap 数法**:`InviteCodeRepository.count_active`(issued+used)≥ cap →
  转 waitlisted,不发码不发邮件。
- **secret 校验失败给 401/503 而非 200+success=False**:server-to-server 路径,
  标准 HTTP 错误更清楚。

## Gotcha

- `code` 字段**只在 status='issued' 时**有值。website 必须按 status 分支
  决定是否发邮件。
- 没配 `INTERNAL_INVITE_SECRET` 时整条 endpoint 失效(503)——这是 opt-in
  设计,部署时必须显式开启。
