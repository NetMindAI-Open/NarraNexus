---
code_file: src/xyz_agent_context/module/lark_module/_lark_command_security.py
stub: false
last_verified: 2026-08-04
---

## 2026-08-04 (五) — 空 flag 回归修复 + 路线表三处收口

round-4 review：① `_recovery_hint("")` 空 flag 撞上 `"" == ""` 落进自路由
分支，吐出 flag 名为空的假路线（本 PR 三轮消灭的失败类被自己新引入，
`if flag and route == flag` 一行修，空 flag 自然落到「查 --help 的
(supports @file) 标注」泛化兜底）；② 扩展名白名单补
log|pdf|docx?|xlsx?|pptx?|py|sh|png|jpe?g（旧 {1,5} 能拦、白名单第一版
漏掉的形态）；③ `--json` 域相关：docs 域是 --format json 的布尔缩写、
只有 base 域是读文件 payload——`_payload_flags_for(tokens)` 按域裁剪
stdin 守卫（与 im --content 例外同构）；④ 表补 `--reference-map`
（--help 自述 "- reads stdin" 而 lark_cli 从不接 stdin，dev 既有缺口
顺手收口，铁律 #8）。

## 2026-08-04 (四) — flag→文件路线表（_FILE_ROUTE）统一守卫与提示

round-3 review：`mail +send --body @file.html` 是同类假成功（探针：文件
不存在照样 ok:true，收件人收到字面量；对照组 `--body-file` 缺文件硬报错），
而 round-2 的「非 im 一律 --content @file」提示恰好把模型往那推（mail 根本
没有 --content）。收敛为一张表 `_FILE_ROUTE`：flag → 真正读文件的 flag
（None=只能内联 / 自身=@relative 展开 / 兄弟 flag 如 --body→--body-file）。
守卫对「route≠自身」的 flag 开火（--body 自动纳入），提示按命中 flag 取
真实路线；未知 flag 给「查 --help 的 (supports @file) 标注」的泛化建议，
绝不点名可能不存在的 flag。唯一的表外补丁：im +messages-* 域内 --content
是 JSON payload 不展开 @（探针 invalid JSON 硬拒），域谓词保留仅为提示
覆盖（tokens[0].lower() 顺带修大小写）。正则改扩展名白名单
(md|markdown|txt|json|html?|csv|xml|ya?ml|rst)：收 .markdown（旧 {1,5}
漏掉）、放 @bob.smith（旧规则误拒纯提及）。旧测试的 docs v2 无效 fixture
（--markdown）换成真实形态，断言按 flag 精确化。

## 2026-08-04 (三) — 等号形态归一化 + 提示纯按域 + 撤销错误的 docs 收窄

round-2 review 三连（全部带 --dry-run 复现）：
1. **`--markdown=@file` 等号形态绕过**（round-1 已提、漏改）：shlex 把
   `--flag=value` 切成一个 token，配对扫描摸不到。新增
   `_split_compound_flag` 归一化，两个守卫（@file 字面量 + 7/29 的
   `$()`/stdin）都同时检查 compound 值——`--content=$(cat x)` 同类缺口
   一并收口。
2. **提示纯按域**：`_recovery_hint(is_im_message)` 单参——im 域**没有任何**
   flag 读文件（`--content @payload.json` 被 CLI 以 invalid JSON 硬拒），
   一律给内联建议；其余域保留 `--content @file`（lark-cli 真机制）。
3. **撤销 docs 收窄**：docs v2 根本没有 `--text/--markdown`（CLI v2 迁移
   校验直接拦）。`_reject_file_reference_bodies` 改为全域无条件（全 CLI
   只有 im +messages-* 带这两个 flag，收窄零收益且对未来新增消息型 flag
   默认失守）；删掉把 CLI 必拒命令固化为「必须放行」的错误测试，改为断言
   真实存在的 `docs +create --content @./report.md` 不受影响。
教训：推新 commit 前必须读完管线上一轮 review。

## 2026-08-04 (二) — @file 守卫收窄到 im +messages-*，7/29 提示语按域分流

两个修正（其一后被 round-2 review 推翻，见下条）：① 曾按「docs 域同名 flag
是文档内容」把守卫收窄到 `_is_im_message_command`——该前提不成立；
② 7/29 的 `_AT_FILE_HINT` 原本对所有 payload flag 统一推荐
`--content @relative/path.md`——对 IM 消息这是把模型往字面量坑里带
（探针实证 2026-08-04：文件存在 + 合法相对路径，`im +messages-send
--markdown @./f.md` 仍原样发出 "@./f.md" 且报 success），改为
`_recovery_hint(flag, is_im_message)`：IM 消息体给内联建议，其余保留
@file 路线。旧测试中 im 用例的「必须含 @」断言随之放宽为「必须给出
可行替代」。

## 2026-08-04 — 拒绝 --text/--markdown 的 @file 字面量（假成功歼灭）

真机事故（claude_code × lark，evt_2b0010e15c2b4548）：模型第一次
--markdown 多词未加引号被拒，改 Write lark_reply.md 后用
`--markdown @lark_reply.md` 重试——这两个 flag 的值就是字面正文，
lark-cli 不做 @file 展开（@./file 约定只在 --json 类 flag 上），
于是把 "@lark_reply.md" 原样发给了人类并返回 success，agent 自认
交付成功。sanitize_command 新增 `_reject_file_reference_bodies`：
--text/--markdown 后跟「单 token、@ 前缀、带文件扩展名」的值即拒绝，
错误文案教模型内联整段引号正文。@提及不受影响（"@张三 …" 带空格、
"@all" 无扩展名，均不命中）；--json @./file 不在守卫范围。
测试 tests/lark_module/test_message_content_guard.py。

## 2026-07-29 — 挡三种「不会报错的 shell 构造」（不是 denylist 回归）

2026-04-21 删掉 shell 元字符 denylist 是对的：不过 shell，`| ; & $ ( )` 就是普通
字节，挡它们只会误伤 "S&P 500" / "$76,000" / markdown 表格，逼 agent 退化成探针式
发 "test"。`test_command_shell_chars_allowed.py` 钉住了这一点。

但**同一个事实还有反方向的后果**：正因为没人展开它们，有三种构造不是"无害的字面
量"，而是静默的数据损坏——lark-cli 会写错内容并返回 success：

| 构造 | 实际发生 |
|---|---|
| `--content "$(cat report.md)"` | 把那 16 个字符写进文档 |
| `--content -` | MCP 路径从不接 stdin（`stdin_data` 恒为 ""），payload 空 |
| `<<'EOF'` | heredoc 是 shell 语法，shlex 只留下一个 `<<EOF` 参数 |

prod 2026-07-29：agent 用第一种把 2746 行 Lark 文档覆盖成一行命令文本，拿到
`{"result":"success"}`，然后汇报「重构完成，缩减 87%」。5 月以来 5 个 agent 共 16 次。

`_reject_unexpandable_shell` 只挡这三种。关键是**整值判定**
（`_is_whole_command_substitution`）：只有当整个参数值就是一个 `$(...)`（配对右括号
落在最后一个字符）才拒——"$(whoami) is a shell builtin (as is pwd)" 这种散文配对括号
在中间，不受影响。拒绝理由必须带 `@file` 写法，否则 agent 会以为是引号问题、换个引号
再试一遍。prompt 侧同步见 [[lark_module.py]] 的 `_NO_SHELL_GUIDE`。

## 2026-04-23 — `auth login` 合法形式扩展（允许 poll 姿势）

`auth login` 原来的合法性判断："必须有 `--scope`，否则一律挡"。这误伤了
**增量授权 poll 阶段**：`auth login --device-code <D>` 是 lark-cli 的
标准 POLL 动作（mint 用 `--no-wait` 拿到 device_code，然后用
`--device-code D` 去换 token），这条命令按协议**不带** `--scope`
（scope 是在 mint 时已经指定过的）。

现场现象（2026-04-23 线上 agent_bbddea03706e / agent_7f357515e25a 对话）：
Agent 按 prompt 教的正确姿势调 `auth login --device-code <D>`，命中
security validator "必须带 --scope" 规则，返回 "Command blocked" →
Agent 以为自己语法错，退回去再 mint 一次（或者拼出
`auth login --scope X --device-code D` 这种非常规组合），形成新的
"多次 mint、orphan URL" 循环。

修法：`auth login` 只要带了 `--scope` **或** `--device-code` 就放行。
裸 `auth login` / `--recommend` / `--domain all` 仍然挡住交给
`lark_permission_advance`（三击 onboarding 的状态机）。

安全边界不变：
- 三击 onboarding 的调用路径绕开 `lark_cli` → 绕开 `validate_command`，
  不受这次改动影响
- `--device-code D` 本身无害——它需要一个之前已经 mint 过的、合法路径
  下的 device_code 才能换到东西；没有新的攻击面
- `--recommend` / 裸 `auth login` / `--domain` 单用仍挡

配套测试：`tests/lark_module/test_auth_login_scope_allowlist.py` 新增
`TestAuthLoginDeviceCodePollAllowed` 三组断言（poll 独用、poll + scope
组合式、确保组合式不绕开 `--recommend` 挡规则）。

## Why it exists

Guards the generic `lark_cli` MCP tool against dangerous or unintended
CLI commands. Without this, an Agent could run `config remove`, `auth
logout`, or inject shell metacharacters.

## Design decisions

- **Whitelist approach** — only known-safe top-level commands are allowed
  (im, contact, calendar, docs, task, drive, schema, api, auth, doctor,
  etc.). Unknown commands are blocked by default.
- **Blocklist for specific subcommands** — even within allowed top-level
  commands, dangerous operations are blocked: `config init`, `config
  remove`, `profile remove`, `auth login`, `auth logout`, `event
  +subscribe`, `update` (CLI self-update).
- **Shell metachar regex** — blocks `|`, `;`, `&`, backtick, `$`, `(`, `)`
  to prevent shell injection. Curly braces `{}` and square brackets `[]`
  are allowed because they appear in JSON `--data` arguments.
- **`sanitize_command` uses `shlex.split`** — safely tokenizes the command
  string. This runs with `shell=False` (subprocess), so shell injection
  is impossible even if regex is bypassed.

## Upstream / downstream

- **Upstream**: `_lark_mcp_tools.py` calls `validate_command()` and
  `sanitize_command()` before every `lark_cli` invocation.
- **Downstream**: none (leaf module).

## Gotchas

- The blocklist checks if the command string *starts with* the blocked
  pattern (after normalization). A creative command like
  `im +messages-send; config remove` would be caught by the shell
  metachar regex (`; ` contains `;`), not by the blocklist.
- `--format json` is NOT blocked but should not be added to Shortcut
  commands (those with `+`). This is documented in Agent instructions,
  not enforced here.
