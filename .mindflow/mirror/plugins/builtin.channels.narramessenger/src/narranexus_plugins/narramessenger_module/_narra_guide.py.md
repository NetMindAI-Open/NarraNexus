---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_narra_guide.py
stub: false
last_verified: 2026-09-09
---


## 2026-09-09 — 文案对齐 1.2 契约：端点也是平台注入

curated `resources/narra-runtime.md` 顶部 banner 改写：平台每次调用注入
**token + 绑定端点**两样；禁传 `--endpoint`（`configure` 已不存在）；点名忽略
NarraMessenger 自家 runtime.md / AGENTS.md 的 `npx ... --endpoint
$NARRA_API_ENDPOINT --token-file .narra/<id>/agent-runtime-token` 流程；并且
点名 `<domain> --help` 的 USAGE 行本身就印着 `npx ... --endpoint --token`
（1.2.1 实况），告诉 agent 那是给独立用户看的、照样忽略——否则我们自己指过去的
「最权威最新」文本会把禁令推翻。新增「调用失败时」段，与 basic_info 的
Product Feedback Duty 同一口径：`agent-token-invalid` / `no_endpoint` /
原本能用的绑定突然鉴权失败 = 平台注入的凭据被拒，不下结论、
`submit_feedback(category="error", dedup_key="narra_cli:<code>")` 上报
（「一次」由工具侧按 key 执行、粒度是**每 agent 每错误码**，不传 key 就完全不去重
——见 [[_basic_info_mcp_tools.py]]），且「团队已被通知」只转述该调用结果里的说法、
不自行断言（资源文件与 `_BUILTIN` 兜底共用同一句
`only if that call's result says so`，`test_narra_guide.py` 对**两份**都钉住——
上一轮就是只改了资源、兜底漏改）；`official-agent-required` /
`no_credential` 是设计内答复，解释即可、不报。绝不贴 token（prod 2026-09-09
agent 断言「平台缓存了过期 token」并把 token 明文贴进聊天，实际是端点错配）。
`_BUILTIN` 兜底带同一套规则（首版只改了半句，Opus 预审 I4 打回），
`test_narra_guide.py` 对资源文件和兜底都钉住 `--endpoint` / `npx` 忽略 /
`submit_feedback` / `official-agent-required`；资源文件里刻意不出现
`@narra-im` 包名（那是安装配方标记，既有测试守着）。

## 2026-07-21 — STOP live-fetching runtime.md; serve a curated reference

**Root cause (dev incident):** `narra_guide` used to live-fetch narra's
`runtime.md` verbatim. That document is written for a runtime that installs /
configures / runs narra-cli **itself** (`npm install @narra-im/narra-cli`,
`configure --endpoint`, `.narra/agent-runtime-token`, and narra-cli's own
`chmod 0700` of `~/.narra-cli`). In OUR architecture narra-cli is
platform-provided via the `narra_cli` MCP tool, so those setup instructions are
actively harmful: with a strong model (Opus 4.8), the agent faithfully followed
the guide, tried to install + `configure` narra-cli in its sandbox, and hit
"narra-cli cannot init its config dir — chmod permission denied". (A weaker model
earlier flailed with Bash and gave a confused "cloud can't execute" apology.)
The `narra_cli` docstring said "don't install", but the detailed guide said "here
is how to install" — and the detailed guide won.

**Fix:** `get_guide()` now returns a STATIC, platform-adapted curated reference
(`resources/narra-runtime.md`) — a strong "platform provides narra-cli, use the
`narra_cli` tool, do NOT install/configure/token/shell-run it" banner plus the
command *shapes* only. No HTTP, no cache, no `backend_base_url`. The whole
live-fetch layer (`_http_get` / `fetch_guide` / TTL cache / aiohttp) was deleted.

## Why it exists

Backs the `narra_guide` MCP tool: an on-demand command reference for the
`narra_cli` passthrough (kept out of every-turn system prompt to save tokens,
same shape as lark's `lark_skill`). narra has no skill packs — one small doc.

## Design decisions

- **Static curated, NOT live.** We deliberately give up "auto-track narra's
  prose" because that prose is setup-oriented and harmful (see above). We do NOT
  lose command freshness: the agent gets exact / latest flags from the live CLI
  via `narra_cli("<domain> --help")`, and the passthrough runs new commands at the
  execution layer regardless. Maintenance is bounded to "narra adds a whole new
  top-level DOMAIN" — which already needs an `ALLOWED_DOMAINS` whitelist edit in
  [[_narra_command_security]], so the two move together.
- **Built-in fallback** keeps the "use the tool, don't set up narra-cli"
  invariant even if the resource file is missing (non-editable wheel edge case).

## Upstream / downstream

- **Called by**: `_narramessenger_mcp_tools.narra_guide` (returns
  `get_guide()`; no credential / network).
- **Reads**: `resources/narra-runtime.md` (the curated reference — the source of
  truth to edit when advertising a new domain).

## Gotchas

- The curated doc's banner NAMES `npm install` / `configure` / `--token` in order
  to FORBID them; that is intentional. What must never appear is the actual
  install RECIPE (`@narra-im/narra-cli`, `./node_modules/.bin/...`) — tests guard
  this.
- If narra ships a genuinely new command surface, update
  `resources/narra-runtime.md` (and the whitelist) — do not re-add live-fetch.
