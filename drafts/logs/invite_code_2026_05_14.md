# 邀请码机制 — PRD + 实现方案

- **Trigger**：用户要为 NarraNexus web version 做邀请码机制，目的是控制注册用户数量
- **Branch / commit**：`main` @ `b4aa5c4`
- **Status**：架构 pivot 后实现完成 + 推送(见文末「## 更新 2026-05-15 — 架构 pivot:SMTP 移到 website」)。本地测邮件链路已可跑通(后端不依赖 cloud 模式),注册仍 cloud-only。
- **核心结论**：用 DB `invite_codes` 表替换现有的单一全局 `INVITE_CODE` 环境变量；Mode B（自动发码 + 全局上限 + waitlist），上限 200；先控用户数，不控 agent 数。

---

## 1. 背景与现状

当前 `backend/auth.py` 是**单一全局 `INVITE_CODE` 环境变量**：所有人共用一个码，`register()` 里 `request.invite_code != INVITE_CODE` 即拒绝。问题：无法追踪、不能撤销、不能限量、一处泄漏全线失守。

按铁律 #2（不做向后兼容），整个替换成基于数据库的唯一邀请码机制。

## 2. 需求（已确认）

| 需求 | 决策 |
|---|---|
| 邀请码唯一 | DB `unique` 约束 + 单次消费（用后即焚） |
| 可从 website 申请 | website 申请页 + 后端 public endpoint `/api/invite/request` |
| 用户填邮箱，我们发码 | 新增 mailer 工具，申请时自动生成并发邮件 |
| 用后标记 used | `register()` 里原子条件 UPDATE 消费 |
| **控量模式** | **Mode B：自动发码 + 全局上限 + waitlist** |
| **上限** | **200**（`INVITE_AUTO_ISSUE_CAP=200`） |
| **控什么** | **先控注册用户数**，不控 agent 数（一个用户仍可建多个 agent；如需卡 agent 总量另开 PRD 做 per-user agent 上限） |

## 3. 控量模式：Mode B

`/request` 时一律生成 code：
- 当前 `status ∈ {issued, used}` 的行数 < 200 → 状态 `issued`，发邮件
- ≥ 200 → 状态 `waitlisted`，**不发邮件**
- 管理员放行：`waitlisted → issued` 并补发邮件

表结构同时兼容 Mode C（审批队列），以后想加审批不用改表。

## 4. 数据模型

新增表 `invite_codes`，注册进 `utils/schema_registry.py`：

| 列 | 类型 (sqlite / mysql) | 说明 |
|---|---|---|
| `id` | INTEGER / BIGINT UNSIGNED PK auto | |
| `code` | TEXT / VARCHAR(32) **unique** | 邀请码本体，如 `NX-7K9MQ2WX` |
| `email` | TEXT / VARCHAR(255) | 申请邮箱 |
| `status` | TEXT / VARCHAR(16) | `issued` \| `used` \| `waitlisted` \| `revoked` |
| `source` | TEXT / VARCHAR(32) | 申请来源，默认 `website` |
| `email_sent` | INTEGER / TINYINT | 邮件是否真发出去了 |
| `created_at` | TEXT / DATETIME(6) | |
| `issued_at` | TEXT / DATETIME(6) nullable | 转为 issued / 邮件下发时间 |
| `used_at` | TEXT / DATETIME(6) nullable | 被注册消费的时间 |
| `used_by_user_id` | TEXT / VARCHAR(128) nullable | 谁用的 |

索引：`unique(code)`、`idx(email)`、`idx(status)`。

**状态流转**：`request` 生成 code → 未超 cap 则 `issued`（发邮件），超 cap 则 `waitlisted`（不发）→ 管理员可 `waitlisted→issued`（补发）→ 注册消费 `issued→used` → 管理员可 `→revoked`。

## 5. 邀请码生成算法（current design v1 — 后续有优化空间）

> 用户决定：本节为**当前设计 v1**，先按此实现跑通；生成算法后续有优化空间（如更长字符空间、分段校验位、可读性分组等），不在 v1 范围内。

### 结论：CSPRNG 随机短串 + DB unique 约束兜底，**不用** snowflake / 号段模式

用户参考的 juejin 文章（7137315582822580238）讲的是**分布式高并发发号器**（snowflake / 美团 Leaf 号段模式）——解决的是"每秒百万级、有序、多节点不冲突"的数字 ID。邀请码的需求恰好相反：

| 维度 | 发号器（juejin） | 邀请码 |
|---|---|---|
| 量级 | 百万/秒 | 总共几百个 |
| 是否要有序 | 要 | **不能要**——有序=可枚举 |
| 是否要人可输入 | 不要（64-bit long） | **要**（短、无歧义字符） |
| 是否要不可猜 | 不要 | **必须**——可猜=200 上限形同虚设 |

所以发号器是错的工具。**自增 ID / sequence 同样不行**（顺序可枚举）。**UUID4** 唯一且随机但 36 字符太长、不好手输。

### 算法

1. **生成**：`secrets.choice`（CSPRNG，不是 `random`）从安全字母表取 8 位。
   - 格式：`NX-` + 8 位
   - 字母表（去掉 `0 1 I L O U` 等易混字符）：`23456789ABCDEFGHJKMNPQRSTVWXYZ`（约 30 字符）
   - 空间 ≈ 30^8 ≈ 6.5×10^11 ≈ 2^39
2. **落库**：`INSERT`，靠 DB 的 `unique(code)` 约束做权威保证。
3. **碰撞重试**：捕获 unique violation → 重新生成 → 重试，最多 5 次。

### "DB 提供还是算法提供"——两者都要，各管一段

- **算法（CSPRNG）**负责：不可猜性 + 格式 + 人可输入
- **DB unique 约束**负责：唯一性的权威保证 + 并发 race 兜底

碰撞概率（生日问题）：200 个码在 2^39 空间里 ≈ 200² / (2 × 5.5×10^11) ≈ 3.6×10^-8，可忽略；即便涨到 1 万个码也才 ~9×10^-5。加上 DB 约束 + 重试，正确性与概率无关——一定不会发出重复码。

## 6. 接口设计

**Public（无需登录）**
- `POST /api/invite/request` — body `{ email }`
  - 幂等：同邮箱已有 `used` → "该邮箱已注册，请直接登录"；已有 `issued` → **重发同一个码**（不新生成，防刷）；已有 `waitlisted` → 提示排队中；否则新建
  - 防刷：复用 `backend/routes/_rate_limiter.py` 的 `SlidingWindowRateLimiter`，按 IP + 按 email 双限流
  - 返回 `{ success, status, message }`，**响应里绝不回显 code**（code 只走邮件，否则限流形同虚设）

**注册改造（`POST /api/auth/register`）**
1. 先 `SELECT` 校验 code 存在且 `status='issued'`（快速报错给 UX）
2. 校验密码/用户名、检查 user 不存在
3. **原子消费**：`UPDATE invite_codes SET status='used', used_at=…, used_by_user_id=… WHERE code=? AND status='issued'`，校验 affected rows == 1（挡住并发抢码）
4. 消费成功后 insert user；若 insert 失败 → 把 code revert 回 `issued`

**Admin（复用现有 admin secret key 鉴权，参照 `backend/routes/admin_quota.py`）**
- `GET /api/admin/invite/codes` — 列表 + 按 status 过滤
- `POST /api/admin/invite/promote` — `waitlisted → issued` + 补发邮件
- `POST /api/admin/invite/revoke` — 撤销

## 7. 邮件发送（SMTP 供应商 — 待用户拍板）

### 可以先用个人邮箱试验吗？可以。

因为我们做的是 **mailer 抽象层 + SMTP 默认实现**，"现在用个人 Gmail → 以后换正式供应商"只是改环境变量，代码不动。

### 建议路径

| 阶段 | 方案 | 说明 |
|---|---|---|
| **开发 / 内测（现在）** | **个人 Gmail SMTP** | `smtp.gmail.com:587`，用 Gmail **App Password**（不是登录密码）。零成本、立即可用。限制：~500 封/天（200 用户内测足够）、发件人显示为你的个人邮箱 |
| **正式上线** | **Resend** 或 **AWS SES** | Resend：API 最干净、免费额度大、送达率好。SES：规模化最便宜，但要验证域名 + 申请退出 sandbox |

### 上线前必做（不论选谁）

用验证过的域名（`narra.nexus`）发信，配好 **SPF / DKIM / DMARC**，否则邀请邮件大概率进垃圾箱。个人 Gmail 内测阶段可以跳过，正式上线必须做。

### 待确认

正式供应商用哪个？（Resend / AWS SES / 公司 SMTP）—— 不阻塞开发，先用个人 Gmail 把链路跑通。

## 8. 各层改动清单

**后端（NarraNexus）**

| 层 | 文件 | 改动 |
|---|---|---|
| Schema | `utils/schema_registry.py` | 新增 `invite_codes` TableDef（改） |
| Schema | `schema/api_schema.py` | 新增 `InviteRequestRequest/Response`、`InviteCode` 实体（改） |
| Repository | `repository/invite_code_repository.py` | `InviteCodeRepository`：`get_by_code/get_by_email/create/consume/count_active/list_all/revoke`（新） |
| Util | `utils/mailer.py` | 邮件抽象层 + SMTP 默认实现（新）。按铁律 #9，抽象接口 + SMTP 默认，不硬绑供应商 |
| Util | `utils/invite_code_gen.py` | 邀请码生成器（CSPRNG + 安全字母表 + 重试）（新） |
| Route | `backend/routes/invite.py` | public `/api/invite/request`（新） |
| Route | `backend/routes/admin_invite.py` | admin list/promote/revoke（新） |
| Route | `backend/routes/auth.py` | `register()` 改造（改） |
| Auth | `backend/auth.py` | 删除 `INVITE_CODE` 常量（改） |
| Bootstrap | `backend/main.py` | 注册两个新 router；CORS 允许 website 域（改） |
| Config | settings / `.env` 示例 | 删 `INVITE_CODE`；加 `SMTP_HOST/PORT/USER/PASSWORD/FROM/TLS`、`INVITE_AUTO_ISSUE_CAP=200`（改） |
| Tier-2 | 每个新文件配套 `.mindflow/mirror/…md`；改动文件刷新对应 mirror md（铁律 #10） | |

**App 前端（NarraNexus/frontend）**：基本不动——`RegisterPage.tsx` 已有 `invite_code` 输入框，错误文案微调即可。

**Website（narranexus-website，独立 repo）**：
- `app/invite/page.tsx` — 申请页：邮箱输入 + 提交 + 结果提示（新）
- `app/api/invite/route.ts` — Next route handler 代理转发到 NarraNexus 后端（新）。走代理而非直连：避免 CORS、藏后端 endpoint、可在这层加 Cloudflare Turnstile / honeypot 挡机器人
- 首页/导航加入口链接

## 9. 关键设计点 & 边界 case

- **原子消费**：条件 UPDATE + affected-rows 校验，挡两人抢同一码；insert user 失败要 revert code
- **防刷**：IP + email 双限流；一个邮箱永远只对应一个有效码（重复申请重发旧码）
- **不回显 code**：`/request` 响应绝不带 code
- **邮件失败**：`email_sent=0` 标记，`/request` 仍返回成功（码已生成），管理员可在列表里看到未送达的重发；邮件失败不阻断流程
- **铁律 #7 双运行方式**：local（SQLite）模式本来就 bypass 注册，邀请码只对 cloud 生效，维持 `_is_cloud_mode()` 分支，不影响桌面端
- **铁律 #2**：`INVITE_CODE` 环境变量彻底删除，不留兼容分支

## 10. 复杂度（结构性维度，铁律 #17）

- 新文件 7 个（mailer / code-gen / repo / 2 route / 2 website），改动文件 ~6 个 + 同等数量 mirror md
- 涉及 4 层：schema / repository / route / util，外加 website 一个独立 repo
- 前置依赖：确定 SMTP 供应商和凭证（**不阻塞**——先用个人 Gmail）
- 风险等级：中。新增表可独立回滚；唯一不可逆点是 `INVITE_CODE` 删除后旧部署环境变量失效——cloud 重新部署即可

## 11. 测试覆盖（铁律 TDD）

- 单测：code 生成唯一性/字母表合法性、`consume` 原子性（并发两次只成功一次）、幂等申请（同邮箱重发旧码）、cap 边界（第 201 个转 waitlist）
- 集成：`/invite/request` → 收码 → `/register` 消费 → 再次 `/register` 同码失败
- mailer：mock SMTP，验证发送失败不阻断 `/request`

## 12. Next step

- [ ] 用户拍板正式 SMTP 供应商（不阻塞，先用个人 Gmail 跑通链路）
- [x] 进入实现：按第 8 节逐文件做，先写测试（TDD）
- [x] 实现前 Read 对应 mirror md（铁律 #3）
- [x] website 改动在 `narranexus-website` 独立 repo，单独 commit
- [ ] 已确认范围：只控用户数。若后续要控 agent 总量 → 另开 per-user agent 上限 PRD

---

## 更新 2026-05-14 — 实现完成

**Branch**：两个 repo 都推到 `invitation_code_2026_05_14`
- NarraNexus：commit `454922e`
- narranexus-website：commit `a70de9d`

**Status**：done（待用户配 SMTP + 自测）

### 落地内容

后端（NarraNexus）：
- `invite_codes` 表（`schema_registry.py`）+ `InviteCode` schema
- `invite_code_gen.py`（CSPRNG 生成器）+ `mailer.py`（通用 SMTP，stdlib，未配置即 no-op）
- `InviteCodeRepository`：`consume` 是单条带条件 UPDATE，并发抢码的 race guard
- `POST /api/invite/request`（public，JWT 豁免）：幂等 + IP/email 双限流 + Mode B cap(200)
- `/api/admin/invite` list/promote/revoke（staff JWT）
- `register()` 改为校验 + 原子消费 DB 码，user insert 失败回滚码
- 删除全局 `INVITE_CODE` 环境变量
- 全部 mirror md 同步（铁律 #10）

website（narranexus-website）：
- `/invite` 申请页 + `/api/invite` 服务端代理路由 + header "Request Access" 导航项

### 取证记录（关键文件）

- 表定义：`src/xyz_agent_context/utils/schema_registry.py`（`invite_codes`）
- 原子消费：`src/xyz_agent_context/repository/invite_code_repository.py::consume`
- 注册改造：`backend/routes/auth.py::register`
- cap 配置：`backend/config.py::Settings.invite_auto_issue_cap`
- 测试：`tests/{utils,repository,backend}/test_invite*` —— 26 个新测试全过，
  `tests/{backend,repository,schema,utils}` 全套 190 passed / 3 skipped 无回归
- ruff 干净；pyright 仅报 4 个 auth.py **既有** issue（login/update_agent/
  delete_agent），新文件 0 error

### Next step（待用户）

- 配置 SMTP 环境变量（先个人 Gmail App Password 跑链路）：
  `SMTP_HOST/SMTP_PORT/SMTP_USER/SMTP_PASSWORD/SMTP_FROM`
- 云端部署设 `INVITE_AUTO_ISSUE_CAP`（默认已是 200）、删掉旧的 `INVITE_CODE` 环境变量
- website 部署设 `NARRANEXUS_API_URL`（默认 `https://agent.narra.nexus`）
- 自测：申请 → 收码 → 注册 → 重复码被拒；cap 触发 waitlist
- 两个 repo 的 PR review + merge
- 邀请码生成算法后续优化（见 §5，v1 已够用）

---

## 更新 2026-05-15 — 架构 pivot:SMTP 移到 website

### 起因

本地起 website 测申请,邀请码邮件没发出来。两层原因复合:
1. 本地后端跑 SQLite(local 模式),原 `/api/invite/request` 第一行 `_is_cloud_mode()` 守卫直接 return,根本没到 mailer
2. 项目的 `.env` → `os.environ` 桥**只转发 4 个 API key**(`settings.py::_API_KEY_FIELDS`),`SMTP_*` 和 `DB_HOST` 进了 `settings` 对象但没进 `os.environ`,而 `mailer.py` / `_is_cloud_mode()` 都是直接读 `os.environ`,所以看不到

用户提的架构观察 → 走完全更干净的路:website 才是 marketing funnel 的所在地,SMTP 凭证、UX 文案、限流都应该跟着 funnel 走;NarraNexus 只负责"发码 + 存表 + 注册时消费"。

### 新架构(option B)

```
浏览器 → website /invite 表单 → website /api/invite (Next route handler)
                                  ├─ 校验邮箱 + 限流
                                  ├─ POST NarraNexus /api/invite/internal/issue
                                  │   (X-Internal-Secret header)
                                  ├─ 拿到 {status, code}
                                  └─ 若 issued → 用 nodemailer 发邮件

注册:NarraNexus /api/auth/register → InviteCodeRepository.consume(原子)
```

`code` 在 server-to-server 这一段确实回传给 website server(它要发邮件),
但不到浏览器。

### NarraNexus 改动

- `backend/routes/invite.py`:重写为 `POST /api/invite/internal/issue`
  - `X-Internal-Secret` header 鉴权(匹配 env `INTERNAL_INVITE_SECRET`)
  - 去掉 `_is_cloud_mode()` 守卫(invite_codes 是张表,SQLite/MySQL 都能落)
  - 去掉发邮件逻辑(`_send_code_email` 删除)
  - 响应**回传 code**
  - 限流改为交由 website 那一边在公共边缘做
- **删除** `src/xyz_agent_context/utils/mailer.py` + 对应 mirror md
- `backend/auth.py::AUTH_EXEMPT_PATHS`:`/api/invite/request` → `/api/invite/internal/issue`
- `admin_invite.py::promote`:不再补发邮件(NarraNexus 没有 SMTP 了),返回 `{code, email}` 给运营手动通知
- `.env.cloud.example`:删 `SMTP_*`,加 `INTERNAL_INVITE_SECRET`
- `api_schema.py`:删除短命的 `InviteRequestRequest/Response`(无人引用)
- 测试改造:29 测全过(secret 强制 + idempotency + cap + register 端到端)
- 所有 mirror md 同步刷新

### website 改动(下一步)

- 加 `nodemailer` 依赖,新建 `lib/mailer.ts`(SMTP 发邮件)
- 重写 `app/api/invite/route.ts`:校验 → 限流 → 调 NarraNexus 内部 API → 发邮件
- `.env.local` 加 `INTERNAL_INVITE_SECRET` + `SMTP_*`(凭证从 NarraNexus `.env` 迁移过来)

### 取证记录(关键改动)

- 新 endpoint:`backend/routes/invite.py::issue_invite`
- 鉴权:`backend/routes/invite.py::_require_internal_secret`
- 测试:`tests/backend/test_invite_routes.py` —— 含 secret 强制 + 503 fail-closed
- 设计 mirror:`.mindflow/mirror/backend/routes/invite.py.md` 的 2026-05-15 dated entry

### 本地能测什么(架构 pivot 之后)

| 操作 | 本地能跑通 |
|---|---|
| 申请邀请码 + 收到邮件 | ✅(SQLite 也行,SMTP 在 website 端) |
| 码落入 `invite_codes` 表 | ✅ |
| 用邀请码注册账号 | ❌ 仍要 cloud 模式(`register()` 的 `_is_cloud_mode` 是 pre-existing 设计) |

### Next step

- 完成 website 那侧改动(nodemailer + route rewrite + .env)
- 用户配 `INTERNAL_INVITE_SECRET`(两边一致)
- 本地端到端验证邮件链路
- 部署 NarraNexus 到 EC2 + website 到生产
- 真实环境测注册闭环

---

## 更新 2026-05-15 (#2) — Secret 机制 + .env 桥扩展 + 当前架构快照

承接上一节(option B 架构 pivot 落地)。这一节是**当前架构的完整快照**和
**`INTERNAL_INVITE_SECRET` 的运维手册**——重启会话来人也能 1 分钟 onboard。

### 当前架构(option B 落地后)

```
浏览器 → /invite 表单 → website server (Next, narranexus-website)
                            │  1. 校验 email + per-IP 限流
                            │  2. POST /api/invite/internal/issue
                            │     Header: X-Internal-Secret: <SECRET>
                            ↓
                       NarraNexus backend
                            │  3. 校验 X-Internal-Secret
                            │  4. 幂等 / cap / 生成码 / 写 invite_codes 表
                            │  5. 返回 {status, code}
                            ↑
                       website server
                            │  6. 若 status='issued' → nodemailer 发 SMTP
                            │  7. 返回浏览器 {status, message},绝不带 code
                            ↑
                       浏览器看到 "Invite code sent"
                            ↓
                       邮箱收到邀请码 → 到 agent.narra.nexus 注册
                            ↓
                       NarraNexus /api/auth/register
                            │  8. InviteCodeRepository.consume 原子消费
                            │  9. 建 user 行
                            ↓
                       注册成功
```

**职责划分**:
- **website**:申请 UI、限流、SMTP 发邮件、入口
- **NarraNexus**:码生成 + 落表 + 幂等 + cap + 注册时原子消费 + admin

### INTERNAL_INVITE_SECRET — 唯一鉴权

`/api/invite/internal/issue` 是**公网可达**的(挂在 `agent.narra.nexus` 下),
任何人都能 POST 它。`X-Internal-Secret` header + 服务端 env 变量比对,
**两边相等才放行**——挡掉外部刷码 / 拿别人邮箱钓鱼 / 灌爆 RDS 的滥用。

校验逻辑(`backend/routes/invite.py::_require_internal_secret`):
```python
expected = os.environ.get("INTERNAL_INVITE_SECRET", "")
if not expected:                # 没配 → 503,fail-closed
    raise HTTPException(503, ...)
if request.headers.get("X-Internal-Secret", "") != expected:
    raise HTTPException(401, ...)
```

JWT 这里不合适——调用方是另一个 server,不是用户。HTTPS 加密传输,
secret 是"对暗号";两者职责不同。

### Secret 在两个 repo 各放哪

| 部署位置 | 本地开发 | 生产 |
|---|---|---|
| **narranexus-website** | `.env.local`(gitignored,`.env*.local` 规则) | 部署平台环境变量(Vercel UI / Docker `-e` / systemd `Environment=` 等) |
| **NarraNexus** | `.env`(gitignored,见下方 `.env 桥`)或启动命令前置 | EC2 上 `.env`、systemd unit、Docker env、Secrets Manager 等 |
| **占位模板**(committed) | — | `narranexus-website` 暂无;NarraNexus 在 `.env.cloud.example` 里有 `INTERNAL_INVITE_SECRET=CHANGE_ME_TO_...` 占位 |

**真值永远不进 git**。占位字符串可以进。

### NarraNexus `.env` 桥的陷阱 + 这次的修复

`src/xyz_agent_context/settings.py` 在 import 时读 `.env` 并选择性注入
`os.environ`。原本只白名单了 4 个 LLM API key,**其他变量进了 `settings`
对象但没进 `os.environ`**——而 `backend/routes/invite.py` 用
`os.environ.get("INTERNAL_INVITE_SECRET")` 直接读,以及 `backend/config.py`
用 `os.getenv("INVITE_AUTO_ISSUE_CAP")` 直接读,所以放在 `.env` 里
**没用**。

**修复**:加一个并列的白名单 `_DOTENV_PASSTHROUGH`,把这类"需要进
`os.environ` 但不是 API key"的变量列出来。改动在 `settings.py` 同一处:

```python
_API_KEY_FIELDS = {"OPENAI_API_KEY", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"}
_DOTENV_PASSTHROUGH = {
    "INTERNAL_INVITE_SECRET",  # backend/routes/invite.py
    "INVITE_AUTO_ISSUE_CAP",   # backend/config.py
}
for _k, _v in _dotenv_values.items():
    if not _v:
        continue
    if _k in _API_KEY_FIELDS or _k in _DOTENV_PASSTHROUGH:
        os.environ[_k] = _v
```

**今后新增"通过 os.environ 读的 backend 配置",都要在
`_DOTENV_PASSTHROUGH` 加一行**,否则 `bash run.sh` 启动时它不会从 `.env`
被加载,会很难 debug(没报错,只是悄悄拿不到值)。

### 本地启动 NarraNexus(.env 已生效)

任选一种,**都从 `.env` 加载 secret**,不再需要前置:
```bash
bash run.sh           # 推荐
# 或
make dev-backend
```

`.env` 里相关段(已配置):
```
INVITE_AUTO_ISSUE_CAP=200
INTERNAL_INVITE_SECRET=<64-char hex>
```

注意:NarraNexus 端 **不需要** SMTP_*——邮件由 website 发。

### EC2 生产部署清单

1. **代码**:`git checkout invitation_code_2026_05_14`(merge 后是 main)
2. **环境变量**(EC2 进程读取的位置,通常是 `.env` / systemd unit / docker env):
   - `INTERNAL_INVITE_SECRET=<跟 website 部署一致的强随机>`
   - `INVITE_AUTO_ISSUE_CAP=200`(可选,默认就是 200)
   - 旧的 `INVITE_CODE=...` 可删可留(代码不再读)
3. **数据库**:`auto_migrate` 启动时自动在 RDS 上建 `invite_codes` 表(additive,无风险)
4. **重启后端**

### website 生产部署清单

1. **代码**:同分支
2. **环境变量**(Vercel / 其它):
   - `INTERNAL_INVITE_SECRET=<跟 NarraNexus EC2 一致>`
   - `NARRANEXUS_API_URL=https://agent.narra.nexus`
   - `SMTP_HOST=smtp.gmail.com`(或正式供应商)
   - `SMTP_PORT=587`
   - `SMTP_USER=<...>`
   - `SMTP_PASSWORD=<App Password 或 SES SMTP password>`
   - `SMTP_FROM=<发件人地址,生产建议用 narra.nexus 域>`
3. **重新构建 / 部署 website**

### Secret 轮换

要换 secret:**两边同时改新值,各自重启进程**。中间窗口期(只改一边)
所有 `/api/invite/internal/issue` 调用 401。所以最好部署窗口期通知好,
或者临时让 website fallback 接受两个值(没必要,通常几秒到几十秒的窗口
够了)。

### 当前本地 secret(开发环境,可直接用)

```
INTERNAL_INVITE_SECRET=331c54403150e578ffc377e67e3b4fd27884c1c0b2db829e256730eb4d1b44a0
```

(已写入 `.env`(NarraNexus)+ `.env.local`(website),都 gitignored。
生产换新值前可以一直用这个本地测;生产**必须**用一个全新的
`openssl rand -hex 32`,这个不要带去线上。)

### 本地能 / 不能测什么(再强调)

- ✅ 申请邀请码 + 收到邮件(走完上面流程图第 1-7 步)——SQLite/MySQL/任何模式都行
- ❌ 用码本地注册账号——`register()` 的 `_is_cloud_mode()` guard 是 pre-existing,
  本地除非显式给 `DATABASE_URL=mysql://...`(且 DB_HOST 进 os.environ),
  否则它返回"only available in cloud mode"

注册闭环测试现在最快的路径:EC2 部署 + 本地 website 指过去(改
`.env.local::NARRANEXUS_API_URL`)。

### 改动文件(本次 2026-05-15 #2)

- `src/xyz_agent_context/settings.py` —— 加 `_DOTENV_PASSTHROUGH`
- `.mindflow/mirror/src/xyz_agent_context/settings.py.md` —— 同步
- `.env` —— 加 `INTERNAL_INVITE_SECRET`,删 `SMTP_*`(配 website 那边)
- `narranexus-website/.env.local` —— 强随机替换占位
- 本文档新增本节
