---
code_file: src/xyz_agent_context/agent_framework/providers/driver/drivers/claude_oauth.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — verify_token_live → verify_live,host-oauth 模式也真验

2026-07-23 只给 oauth_token 修了"存在≠健康";host-CLI `oauth` 模式在
user_service 里仍是无条件放行,直到 codex P0 暴露同类谎报。重命名为
`verify_live` 并覆盖两种模式:token 模式逻辑不变;host 模式先 probe()
(凭证文件/Keychain 存在性)快速失败、再跑同一个一发 CLI 调用——
`build_claude_config` 本就按模式选凭证通道(token→env 注入,host→CLI
自己的存储),一发调用天然两用。成功/失败文案改为模式无关措辞。

Review 轮修正(同日,PR #224 Critical):host-oauth 一发**必须先
staging**——to_cli_env 把 CLAUDE_CONFIG_DIR 指向隔离目录(#72 泄漏修复),
其 .credentials.json 只有 `_stage_claude_oauth_credentials`(agent
adapter 每次 spawn 前调)会写;不 staging 则刚 `claude login` 还没跑过
turn 的健康凭证会被验成死的。verify_live 现与 adapter 同步 staging。
返回改三态:executor seam(`executor_seam_active()`,BROKER_URL 或
AGENT_EXECUTOR_URL——review 第 3 轮:只认后者在云上是死守卫)/超时/
staging 失败/CLINotFoundError(resolve_cli_path 对无外部二进制 fail-open
到 bundled,"起不来"不是凭证判决)→ unknown;token 缺失/probe 失败/
CLI 拒绝(claude SDK 把 auth 失败作为进程异常抛出,与 codex 相反,
其余异常即 dead)→ dead。**seam 守卫只罩 host-oauth**:token 模式凭证
随 env 注入、backend 镜像有 claude CLI,控制面上验证有效(2026-07-23
起如此),不得连坐降为 unknown。一发与 agent 同二进制:
ClaudeAgentOptions 带 `cli_path=resolve_cli_path()`(尊重 CLAUDE_CLI_PATH
钉版)。

# claude_oauth.py — Claude subscription driver (host-CLI OAuth + setup-token)

一张 claude_oauth 卡，两种凭据运输层，按 `auth_type` 分：

* `oauth`（历史路径）：host Claude Code CLI 托管凭据。行携带
  `api_key=""`（让 `to_cli_env` 清空 env、CLI 读自己的凭据存储）+
  `auth_ref='claude-cli:~/.claude/.credentials.json'` sentinel；spawn 前
  由 `_stage_claude_oauth_credentials` 把宿主凭据 staged 进隔离目录。
* `oauth_token`：`claude setup-token` 一年期 token 存 `api_key` 列，
  `CLAUDE_CODE_OAUTH_TOKEN` env 注入（官方 headless 通道）。无 staging /
  无 Keychain / 无 auth_ref——token 本身就是凭据。

两种运输层都 `supports_anthropic_server_tools=True`（官方 Anthropic 后端），
helper_llm 槽同卡覆盖（`build_cli_helper_config`，framework=claude_code，
helper 的结构化一次性调用经 CliHelperSDK 走同一 claude CLI）。

## 2026-07-26 — oauth_token 运输层 + probe 诚实化（2026-07-23 事故根治）

事故链：macOS CLI 把 staged 凭据文件一次性导入按 CONFIG_DIR 哈希命名空间
化的 Keychain 条目后只读该条目，冻结副本随宿主 OAuth 家族轮换死亡，而平台
一切修复只写文件——env 注入的 setup-token 完全绕开 CLI 凭据存储。

probe 诚实化（事故第二教训「存在 ≠ 健康」）：token 模式 = 有无 token +
前缀软提示（`sk-ant-oat` 是观察值非契约，绝不做校验门），并把真话指向
`verify_token_live`；oauth 模式明示查的是**无后缀** host Keychain 条目
（隔离 CONFIG_DIR 下 runtime 读的是带哈希后缀条目，「这里有」不保证
runtime 能认证——`_keychain_has_credentials` docstring 的 Caveat 就是事故
里 probe 查错对象的问题）。

`verify_token_live()`：显式 Test 按钮专用的真实一次性 CLI 调用（helper
量级超时上界；单轮无工具不是 agent_loop，设界不违铁律 #14）。绝不在
`probe()` 里跑（Settings 每次加载都调 probe）。永不回显 token。

## 2026-07-07 — helper 槽也由订阅覆盖

新增 `build_cli_helper_config`（framework=claude_code, auth_type 透传, key
透传）。一次登录/一个 token 既服务 agent（build_claude_config）也服务
helper（helper 的结构化调用经 CliHelperSDK 走同一 claude CLI）。

## 2026-07-07 (跟进) — probe 增加 macOS Keychain 回退

Claude Code 在 macOS 上把 OAuth token 存 Keychain(generic password
'Claude Code-credentials'),不写 `~/.claude/.credentials.json`——纯文件
探测在所有 Mac 上误报 '✗ credentials file not found'(CLI 实际能跑)。
oauth 模式的 `probe()` 文件缺失时用 `security find-generic-password` 查
Keychain(仅判存在、不读密文;非 darwin/出错回落文件结论)。
