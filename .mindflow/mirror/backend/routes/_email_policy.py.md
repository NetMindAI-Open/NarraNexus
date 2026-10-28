---
code_file: backend/routes/_email_policy.py
last_verified: 2026-10-28
stub: false
---

# _email_policy.py — 自助注册拒收的邮箱域名

## 为什么存在

`/api/auth/signup/send-code` 与 `/api/auth/signup` 天然无认证，send-code 还消耗
我们的 NetMind 发信通道。一次性邮箱是批量造号最便宜的手段。本模块是
「哪些地址不让注册」的唯一答案，独立成私有模块，路由只 import `is_blocked_email`。

## 设计决策

- **小而显式的 frozenset**（61 个知名一次性邮箱域），diff 可审；否决「vendored
  第三方大列表」——会在我们不知情时变化，且误伤真实邮箱服务商。
- **长期别名 / 转发服务不收**（33mail.com、spamgourmet.com、burnermail.io 已移除）：
  它们背后是用户真实、长期的收件箱，不是用完即弃。`cc.cd`、`maxxspace.com`、
  `nimail.cn` 是有意保留的条目。
- **env 扩展** `NARRANEXUS_BLOCKED_EMAIL_DOMAINS`（逗号分隔，容忍空格/前导 `@`/
  首尾 `.`/大小写），**每次调用读取**；解析后的并集按**原始 env 字符串**做
  `lru_cache`（`_blocked_domains(raw_env)`），env 不变时只是一次字典查找。**不要**把
  缓存改成无参数的（缓存「读 env」本身）：那会把 env 冻在首次调用，`monkeypatch.setenv`
  失效且跨用例互相污染（`test_env_change_is_seen_on_the_next_call` 会红）。但 env 是容器
  启动时注入的：改 `.env` 后必须**重建 backend 容器**才生效（restart 不重读 env
  文件）。对比：auth.py 的 per-IP 限额在 **import 期**读取，同样要重建容器；本黑名单
  在进程内是每次调用读 `os.environ`。
- **按 DNS label 后缀匹配**：列出的域屏蔽自身与所有子域（`eu.mailinator.com`），
  不误伤 `notmailinator.com`，也不被 `mailinator.com.evil.org` 骗到。
- 无 `@` 的字符串返回 False——地址合法性是 `EmailStr` 的职责。

## 上下游

- **被谁用**：`backend/routes/auth.py` 两处：
  - `_refuse_blocked_email`，在 send-code 与 signup 中**先于任何限流桶**执行（被拒
    请求不花 IP 桶、不分配 email key、不调上游）。因此**列表可被免费无限枚举**，这是有意
    的：条目都是公开的知名一次性邮箱域，枚举出来不是情报；挪到 per-IP 桶之后会让打错域名的
    真实用户白花一次额度。中性文案只是不主动提示，不是防护；
  - `_free_credit_withheld`，在 netmind_login 里决定是否发 free-tier 钱包与引导 Agent
    ——兜住直接在 NetMind 注册、绕开上面两条路由的账号（用户照常登录，只是没有免费额度）。
- 拒绝文案在路由侧，刻意中性（不出现 "disposable"，免得告诉探测者换哪个域）。
- 两处日志粒度不同是有意的：signup funnel 记完整 email（排障要定位具体尝试），登录期
  门只记域名（它对每一次登录生效）。

## Gotcha

- **触发**：在列表里加一个真实邮箱服务商的父域（如某个大 ccTLD 二级域）→
  **症状**：该服务商所有用户无法注册 → **根因**：后缀匹配会屏蔽全部子域。`cc.cd`
  就是这类「二级公共后缀」，加条目前确认它是一次性服务自己的域。

测试：`tests/backend/test_email_policy.py`、`tests/backend/test_signup_abuse_limits.py`。
