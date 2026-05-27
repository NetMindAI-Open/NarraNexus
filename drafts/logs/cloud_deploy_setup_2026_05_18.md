# Cloud 部署 setup guide — invite code + templates marketplace

- **Trigger**:本地两个 feature 都验证通过,准备上 cloud。需要把 EC2(NarraNexus)+ website 两侧的配置一次性给清楚
- **覆盖 feature**:
  - **Invite code**(branch:`invitation_code_2026_05_14`,两 repo 同名)—— 邀请码注册门禁
  - **Templates marketplace Phase 2**(branch:`template_sharing_2026_05_18`,两 repo 同名)—— website 一键 install 到 NarraNexus
- **Status**:waiting-for-deploy
- **核心结论**:两 feature 互相独立,可以分别 deploy 也可以一起 deploy;两侧主要工作量是**环境变量**,代码改动这边都做完了。**唯一阻塞 decision** 是 SMTP 供应商(建议先用 Gmail 内测,再迁 Resend)

---

## 0. 部署前的 4 个 decision

| Decision | 选项 | 推荐 |
|---|---|---|
| **SMTP 供应商**(invite email 用) | 个人 Gmail / Workspace Gmail / Resend / AWS SES / SendGrid | **内测**:你的 Gmail App Password;**正式上线**:Resend(最省事)或 SES(便宜) |
| **`INTERNAL_INVITE_SECRET`** | 任意 ≥32 字节随机串 | `openssl rand -hex 32` 生成,两侧填同一个 |
| **branch 策略** | (a) 两个 feature 都 merge 到 main 再 deploy;(b) 分支独立 deploy 测;(c) 一个 deploy 一个 hold | **推荐 (a)**:都已经独立验证过,merge 后一次 deploy 更省事 |
| **DMG release timing** | 立即 / 跟下次 release / 暂不 | **跟下次 release**:Stage B(DMG deep-link)代码已 push 但还没 build/test;非阻塞,等下一个 dmg release 顺手 |

---

## 1. NarraNexus EC2 配置

### 1.1 拉代码

**推荐做法**(decision a):
```bash
# 在本地把两个 feature merge 到 main
git checkout main
git merge invitation_code_2026_05_14
git merge template_sharing_2026_05_18
# 解 conflict(主要是 settings.py 的 _DOTENV_PASSTHROUGH 白名单,
# 两边都加了 entry,合并起来即可:
#   {"INTERNAL_INVITE_SECRET", "INVITE_AUTO_ISSUE_CAP", "BUNDLE_FETCH_ALLOWED_HOSTS"}
# )
git push origin main
# 然后 EC2 上:
ssh ec2-...
cd <NarraNexus 目录>
git fetch && git checkout main && git pull
```

**或独立 deploy**(decision b):
```bash
# EC2 上 checkout 一个特定 feature branch:
git fetch && git checkout invitation_code_2026_05_14 && git pull
# 测好 → 切到下一个:
git checkout template_sharing_2026_05_18 && git pull
```

### 1.2 环境变量(EC2 上无论用 `.env` / systemd / docker env 都行)

| 变量 | feature | 必需 | 取值 |
|---|---|---|---|
| `INTERNAL_INVITE_SECRET` | invite | **是** | 跟 website 一侧设的同一个 64 字符 hex 字符串 |
| `INVITE_AUTO_ISSUE_CAP` | invite | 否 | 默认 `200`;到这个数 cap 后 `/api/invite/request` 转 waitlist |
| `BUNDLE_FETCH_ALLOWED_HOSTS` | templates | 否 | 默认 `narra.nexus,www.narra.nexus`(prod 已包含);要支持其他 bundle 来源(对象存储 host)再扩 |
| `INVITE_CODE`(**legacy**) | — | **删除** | 老的全局 invite code,代码不再读;留着无害但可以删 |

**注意**:如果走 `.env` 加载,要确认 `settings.py::_DOTENV_PASSTHROUGH` 白名单包含上面前 3 个变量(merge 完后应该都在)。否则 `.env` 里有值但 `os.environ` 看不到。

### 1.3 SMTP 不需要在 EC2 这边配 — invite 邮件由 website 端发

NarraNexus 端**不**装 mailer。SMTP 凭证只在 website 那边。

### 1.4 数据库

`invite_codes` 表是 additive 新表,EC2 后端启动时 `auto_migrate()` 自动建在 RDS 上,**不需要手工 migration**。

### 1.5 重启 backend + 验证

```bash
# 重启服务(具体看你们 EC2 启动方式 — systemd / docker / supervisord)
systemctl restart narranexus    # 或对应命令

# 验证 invite 内部 endpoint 活了
curl -X POST https://agent.narra.nexus/api/invite/internal/issue \
  -H "X-Internal-Secret: wrong" \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com"}'
# 期望:HTTP 401("invalid or missing X-Internal-Secret")
# 用 wrong secret 应该被拒,说明 endpoint 注册了且校验在工作

# 验证 templates 内部 endpoint 活了
curl -X POST https://agent.narra.nexus/api/bundle/import/from-url \
  -H "Content-Type: application/json" \
  -d '{"url":"http://localhost:1/foo"}'
# 期望:HTTP 401 (auth 要求) 或 403 (URL allowlist 拒) — 不能是 404
```

---

## 2. website 配置(Vercel / 你们的 Next 托管)

### 2.1 拉代码 + 依赖

```bash
# (如果跟 NarraNexus 一样 merge 到 master 用)
git checkout master && git pull   # narranexus-website 默认分支是 master

# Vercel 自动跑 npm install + npm run build
# build 会先跑 npm run verify:templates(prebuild hook),
# 校验 lib/templates.ts 里每个模板的 sha256 + size 跟 public/templates/
# 文件对得上,对不上则 build fail。
```

新增依赖:
- `nodemailer`(invite)
- `tsx`(verify-templates 脚本)
都在 package.json 里,Vercel 跑 `npm install` 自动装。

### 2.2 环境变量(Vercel UI / Cloudflare Pages / 任何托管的 env 设置面板)

| 变量 | feature | 必需 | 取值 |
|---|---|---|---|
| `INTERNAL_INVITE_SECRET` | invite | **是** | 跟 EC2 同一个值 |
| `NARRANEXUS_API_URL` | invite | **是** | `https://agent.narra.nexus`(prod)或对应 staging URL |
| `NEXT_PUBLIC_NARRANEXUS_APP_URL` | templates | **是** | `https://agent.narra.nexus`(同上,但 `NEXT_PUBLIC_` 前缀让浏览器端也能读) |
| `SMTP_HOST` | invite | **是** | `smtp.gmail.com` / `smtp.resend.com` / `email-smtp.us-east-1.amazonaws.com` 等 |
| `SMTP_PORT` | invite | **是** | `587`(STARTTLS) |
| `SMTP_USER` | invite | **是** | 看供应商:Gmail 是邮箱地址;Resend 是 `resend`;SES 是 SMTP credential 用户名 |
| `SMTP_PASSWORD` | invite | **是** | Gmail App Password(不是登录密码)/ Resend API key / SES SMTP password |
| `SMTP_FROM` | invite | 否 | 默认等于 `SMTP_USER`。**强烈推荐**改成 `invite@narra.nexus`(域名地址,送达率好,看着也专业) |
| `SMTP_USE_TLS` | invite | 否 | 默认 `true`(走 STARTTLS) |

### 2.3 SMTP 选型 + DNS

**内测阶段**(几十封邮件):
- Gmail / Workspace + App Password 就行
- 不用配 DNS,但发件人显示你的个人邮箱

**正式上线**(>50 封 / 公开):**强烈建议**用域名地址发信 + 配 SPF / DKIM / DMARC,不然 80% 进垃圾箱:
- 选 Resend(推荐):注册 → 在 dashboard 验证域名 narra.nexus → 跟着 UI 提示加 TXT 记录到 DNS → 拿 API key 当 `SMTP_PASSWORD`(`SMTP_USER=resend`,`SMTP_HOST=smtp.resend.com`,`SMTP_PORT=587`)
- 选 SES:更便宜但要 ~半天:Apple Dev console 验证域名 → 跑脚本生成 SPF/DKIM TXT 记录 → 申请退出 sandbox 模式(初期限制 200/天)→ 拿 SMTP credentials

### 2.4 部署

Vercel 自动:push 到 master → 触发 build → npm install + verify:templates + next build → 部署

部署后:
```bash
# 验证 templates 页活了
curl -I https://narra.nexus/templates
# 期望:HTTP 200

# 验证 invite 申请 API 活了
curl -X POST https://narra.nexus/api/invite -H "Content-Type: application/json" -d '{"email":"test@example.com"}'
# 期望:HTTP 200 + JSON 响应
# (如果 SMTP_HOST 未配,响应里 success=true 但邮件不会发出去 — 看 Vercel function logs)
```

---

## 3. 端到端联调(部署完后做一次)

### 3.1 Invite code 全链路

1. 浏览器开 `https://narra.nexus/invite`
2. 填一个你能收到的邮箱 → 提交
3. **你的邮箱**应该收到邀请码邮件(含 `NX-XXXXXXXX` 格式的码)
   - 没收到?查 Vercel function logs;查 NarraNexus EC2 logs(`agent.narra.nexus/api/admin/invite/codes` 看 admin 列表里有没有这条 + `email_sent` 字段)
4. 浏览器开 `https://agent.narra.nexus/register`(没有这个 route 就先 login → 找注册入口)
5. 填用户名/密码 + 上面那个码 → 注册
6. 应该成功登录进 dashboard
7. **测重放**:换个浏览器,用同一个码再注册 → 应该被拒("This invite code has already been used")

### 3.2 Templates marketplace 全链路(Cloud install)

1. 浏览器开 `https://narra.nexus/templates`
2. 看到 Financial Morning Briefing 卡片 → 点进详情
3. 点 `Install in NarraNexus ▾` → 选 `Cloud`
4. **新 tab** 打开 `https://agent.narra.nexus/app/templates/install?url=...&sha256=...`
5. 如果没登录 → 走 login 流程(query params 可能丢,登完手动重新点 install 按钮)
6. 登录后看到 "Install template / Fetching template..." → 几秒后跳 "Review"
7. 看 manifest 预览(6 agents、2 skills 等)→ 点 `Import now`
8. 跳 "Done" 页 → dashboard 应该多出 6 个 agent(Briefing Maestro 等)

### 3.3 Templates 列表的 sha256 校验(deploy 时自动跑)

`npm run verify:templates` 是 Vercel build 的 prebuild step。如果 `lib/templates.ts` 里 sha256 跟 `public/templates/foo.nxbundle` 的文件 sha256 对不上,**build 直接 fail**,prod 不会上去有问题的数据。这个 guardrail 已经设好。

---

## 4. 没做的(known gaps,后续 PR)

### 4.1 DMG 一键 install(Stage B 模板分享)

代码已经 push 到 `template_sharing_2026_05_18`(Tauri 端 + 前端 listener + website dropdown 的 Desktop 选项),**未 build + 装机测试**。下次出 DMG release 时:
1. `bash scripts/build-desktop.sh`(CI 跑或本地)
2. 装新 DMG 到 `/Applications`
3. 浏览器测 `narranexus://install?...` 链路
4. 测通后正常 release

不阻塞 invite + templates web 端上线。

### 4.2 模板分享 Phase 3(用户分享自己的 agent 出来)

Share-safe export + 上传 → marketplace。设计写在 `drafts/logs/template_sharing_2026_05_18.md` §8,**尚未实现**。等用户量起来再开工。

### 4.3 Templates 上 B2(对象存储)

当前 bundle 文件在 `narranexus-website/public/templates/`(Phase 1 决策)。模板数量长上去后 git 仓库会膨胀。**迁移代价**:`lib/templates.ts::bundle_url` 字符串改成对象存储 URL,前端代码 0 改动,verify-templates 脚本会 skip 绝对 URL(已写)。

### 4.4 SMTP 域名 + DNS

正式上线前**必做**:
1. 选定 SMTP 供应商(Resend / SES 等)
2. 在 narra.nexus 域名 DNS 加 SPF + DKIM + DMARC 记录(供应商 dashboard 会给具体记录值)
3. `SMTP_FROM` 改成 `invite@narra.nexus`

---

## 5. Rollback 预案

两个 feature 互相独立,可以分别 rollback:

### Invite code rollback

- **EC2**:`git checkout <pre-invite commit>` → restart backend
- **`invite_codes` 表**:additive,留着不影响。如果想清理:`DROP TABLE invite_codes`(只有这一张是这次新建的)
- **Vercel**:回滚 Vercel deployment 到上一版本(Vercel UI 一键)
- **env vars**:可以保留,旧代码会忽略

### Templates rollback

- **EC2**:`git checkout <pre-templates commit>` → restart;前端会找不到 `/app/templates/install` 路由 → 用户看到 404,但其他功能不影响
- **Vercel**:回滚 deployment
- **website 的 `/templates` 页**:旧版本没有,直接 404,无副作用

### 都失败

把两侧都回到 `main` 当前 commit 即可(invite_codes 表留着无害)。

---

## 6. 快速参考:env vars 一览

**NarraNexus EC2**(`.env` 或 systemd / docker env):
```
INTERNAL_INVITE_SECRET=<openssl rand -hex 32>
INVITE_AUTO_ISSUE_CAP=200
BUNDLE_FETCH_ALLOWED_HOSTS=narra.nexus,www.narra.nexus
# (旧 INVITE_CODE 可以删除)
```

**website**(Vercel env vars 面板):
```
INTERNAL_INVITE_SECRET=<跟 EC2 同一个值>
NARRANEXUS_API_URL=https://agent.narra.nexus
NEXT_PUBLIC_NARRANEXUS_APP_URL=https://agent.narra.nexus
SMTP_HOST=smtp.resend.com               # 或 smtp.gmail.com 内测
SMTP_PORT=587
SMTP_USER=resend                        # Gmail: 你的邮箱地址
SMTP_PASSWORD=<API key 或 App Password>
SMTP_FROM=invite@narra.nexus            # 推荐域名地址
SMTP_USE_TLS=true
```

---

## 7. Next step

- [ ] 拍板 SMTP 供应商
- [ ] 生成 `INTERNAL_INVITE_SECRET` 强随机值
- [ ] 决定 branch 策略(merge 到 main 还是分支独立 deploy)
- [ ] EC2 拉代码 + 配 env + 重启
- [ ] Vercel 拉代码 + 配 env + redeploy
- [ ] 走 §3 的全链路联调
- [ ] 若选了 Resend / SES:配 DNS(SPF/DKIM/DMARC)
- [ ] 监控:几天内看 Vercel function logs(SMTP 失败率)+ NarraNexus admin invite codes 列表(`email_sent=0` 的码需要重发)
