---
code_file: src/xyz_agent_context/module/lark_module/_lark_command_security.py
stub: false
last_verified: 2026-08-05
---

## 2026-08-05 (三) — 注释精度第二轮 + 孪生 guard 同源死分支

**同一处、反方向又犯了一次。** 上一轮为了纠正「零误伤」的过度承诺，把边界写成
「正文**以 flag 名开头**仍会被拦」—— 这句话又比代码**说得多**。实测：
`--app-secret is bad` / `--app-secret-stdin is worse` / `--app-secret  =  x` /
`--foo bar --app-secret=x` **全部放行**，因为它们是单个 token、要么不含 `=`、
要么 `=` 前的部分不等于 flag 名，`name` 取到整句话，匹配不上。

真实边界**恰好两种**：正文整体等于 blocked flag，或首个 `=` 之前的部分等于。
已按 `_split_compound_flag` 的语义重写注释，并把**放行的一侧也钉成测试**
(`test_body_merely_containing_a_flag_name_still_sends`)——只钉「被拦」的一侧，
注释和函数名仍可能漂移到更大的集合；两侧都钉住，边界才锁死。测试也随之改名为
`test_body_that_parses_as_a_blocked_flag_token_is_refused_by_design`（原名
`..._body_starting_with_a_flag_name_...` 本身就断言了那个更宽的集合）。

教训：**这个 PR 系列的全部价值就是「注释不能比代码保证得强」，而修正注释时
最容易再犯一次同样的错。** 写边界时不要用自然语言概括，直接引用代码的判定
语义，并用双向测试固定。

顺带（铁律 #8）：孪生 `_narra_command_security.py:140-141` 有**一模一样**的
不可达空命令分支（开头已 `if not command or not command.strip()` 返回，
`shlex.split` 对非空 stripped 输入不会返回 `[]`），一并删除。

**第三条同源死分支**（第二轮 review 抓到，讽刺的是本 PR 标题就叫 sweep the
twin guard，扫了隔壁模块却漏了本函数下面 15 行）：domain 白名单前的
`tokens = stripped.split(); if not tokens: return "Empty command after parsing"`
判据完全相同——`stripped` 非空且已去空白，`str.split()` 不可能返回 `[]`。已删。
**教训：「顺手扫相邻代码」要先扫正在编辑的那个函数，再去扫隔壁文件。**

## 2026-08-05 (三) — review 收尾：把「零误伤」这句话说准

PR #237 合入后 review 的两条 🟢 收尾。

**① 不变量的真实边界要写出来，别让注释比代码保证得强。**
flag 检查是**整 token 等值**，但 `_split_compound_flag` 对任何 `--` 开头且含
`=` 的 token 都做 partition、不看空格。所以「正文永远够不着控制面规则」有一个
针尖大的例外，**恰好两种形状**（就是 `_split_compound_flag` 的语义）：正文
**整体等于**某个 blocked flag（`--app-secret`），或正文**首个 `=` 之前的部分
整体等于**（`--app-secret=xyz please`）。**「以 flag 名开头」不是边界** ——
`--app-secret is bad` 是单个 token、不含 `=`，`name` 取到整句话，照样放行。

这是**刻意的取舍**而不是缺陷：dev 的子串匹配同样拦这两个形状（方向上没有放宽），
而「真实消息以 `--app-secret` 开头」概率约等于零 —— 拿这个针尖换掉
「secret 进 argv」那个真缺口是划算的。原注释写成「正文是带空格的整 token，
永远不等于 flag 名」，读起来像零误伤，将来有人照着推理会得出错误结论。已把
边界写进注释，并加 `test_body_starting_with_a_flag_name_is_refused_by_design`
把它**钉成显式用例**——已知取舍要可见，不能留白。

**② 删掉不可达的空命令分支。**
`validate_command` 开头已对 `not command.strip()` 返回，`stripped` 因此非空且
已去空白；实测这种输入 `shlex.split` 不可能返回 `[]`（`'""'`/`"''"` → `['']`，
`'#c'` → `['#c']`，`'\x00'` → `['\x00']`；只有纯空白才 `[]`，而那到不了这里）。
所以上一轮新增的 `if not parsed: return "Empty command"` 是死分支，且与下方
`"Empty command after parsing"` 对同一输入给出两种 reason，徒增上层日志分析成本。
删除，保留原有那条。正是这次刚以铁律 #2/#8 之名从 `_reject_unexpandable_shell`
删掉的同一类东西。

## 2026-08-05 (三) — 正文永远够不着控制面规则（prod 事故修复）

**事故**：08-04 NarraNexusPM 的每日晨报没能完整发进 Nexus-算法only群，
群里只收到一条 459 字的 `--text` 缩写版，Owner 追问「你今天在群里发的消息
怎么变得这么简单」。

**根因不是长度、不是 `--markdown`，是匹配位置**：`validate_command` 用
`f" {pattern}" in lower` 拿 **整条命令串** 去撞 `BLOCKED_PATTERNS`，而这条串里
含 `--markdown "……"` 引号内的消息正文。裸词 `"update"`（本意挡 lark-cli 自更新
子命令）于是把每一份含 "update"/"updated" 的日报都拒了，还回了一条
`Blocked command: 'update' — use the dedicated MCP tool instead` ——
指向一个 agent 根本没敲过的命令。

**代价**：agent 探测了 8 次（往真实会话里发 "test"、"Day 57 晨报" 探针），
其中 `"Day 57 report test - no update word"` 这句因为自己含 update 也被拒，
于是它得出**错误结论**「`--markdown` 长文本被 sandbox 拦截」，降级发了缩写版，
并把这个错误解释回复给了团队。DB 佐证：`event_stream` 里
`Blocked command: 'update'` 共 42 次命中（8 events / 3 agents，自 05-13），
而其余 6 条 BLOCKED_PATTERNS **一次都没命中过** —— 即该 guard 历史上 100%
的触发都是误杀。

**同一缺陷的另外两处**：`BLOCKED_FLAGS` 的 `flag in stripped`（正文里提一句
「别把 --app-secret 写进命令行」就发不出去）、heredoc 检查的
`tok.startswith("<<")`（正文以 `<<` 开头即被判成 shell 语法）。

**修法 —— 只改「看哪里」，不改「允许什么」**：两条规则都改成读
**shlex 分词后的 token**，不再碰扁平串 —— BLOCKED_PATTERNS 做**前导 token
锚定**（第 0 位永远是 domain、第 1 位是子命令，正文只能待在 flag 之后，
天然够不着），BLOCKED_FLAGS 做**整 token 等值**（事故正文
`"Security note: never pass --app-secret on the command line."` 是一个
**带空格的整 token**，永远不等于 flag 名）。`ALLOWED_DOMAINS` 一字未动、
denylist 条目一条未删、fail-open 保持，所以 lark-cli 广度零收窄。

> **弯路记录（PR #237 review 抓到）**：第一版曾引入 `_control_tokens()`
> 投影层，把「payload flag 的下一个 token」整个剔除。这对 pattern 检查是
> **冗余**的（前导锚定本就够不着正文），对 flag 检查是**净负收益** ——
> `--content --app-secret SEK` 里的 `--app-secret` 会跟着 payload 值一起被
> 丢掉，相对 dev **真实收窄了护栏**。要点：这条 denylist 防的是「secret 进
> argv」，泄漏发生在 `execve` 那一刻（`ps` / 进程审计 / 崩溃日志都看得到），
> **与 lark-cli 怎么解析这对 flag 无关**。投影层已删除，回到与
> `_narra_command_security.py:145-159` 一致的形状（全 token 前导锚定 +
> 全 token 等值），代码更少且没有这个缺口。

**heredoc 用「整形」而非「前缀」**：`_is_heredoc_token()` 只认
`<<[-]?DELIM` 这种**整个 token 就是操作符**的形态。这里踩过一次：最初按
「控制位」区分，结果打穿了既有用例 `--markdown <<-EOF` —— 那是真 heredoc
（会把字面量 `<<-EOF` 当正文发出去），它恰好处在 payload 值位。形状判定
两边都对：`<<Summary>> Day 57 went fine.` 放行，`<<-EOF` 照旧拦。
正则里**不含 `\s*`**：token 出自 `shlex.split`，真操作符内部不可能有空白
（有空白就说明是引号包起来的正文），加了 `\s*` 只会把 `<< EOF` 这种正文
误判成 heredoc —— 正是本次要消灭的那类误杀（review Minor 2）。

**解析失败 fail closed，且只解析一次**：从 token 读规则意味着解析失败必须
**拒绝**而非放行，否则 `auth logout "` 会因为拿不到 token 而跳过全部规则
（旧的子串匹配只是**碰巧**挡住了它）。`validate_command` 统一 shlex 一次，
`_reject_unexpandable_shell` 改收已解析的 token —— 它原本那个
「解析失败就放行、留给 sanitize_command 报错」的分支在新顺序下已是死代码，
且与新的 fail-closed 语义打架，一并删除（review Minor 3，铁律 #2/#8）。
注意 domain/auth 段仍用朴素 `stripped.split()`，本次**不动**它的分词语义，
故用 `parsed` / `tokens` 两个名字明确区分。

**边界（与 2026-07-29 那条一脉相承）**：payload **完整性**检查（stdin `-`、
整值 `$(...)`）**可以**读值，因为它问的是「这个值会不会原样送达」，但只做
**整值**判定，永不看子串 —— 散文永远不会整体等于 `-` 或 `$(...)`。这条边界
本身没变，这次只是把它和「控制面规则」明确分开了。

**不变量测试**：`tests/lark_module/test_command_payload_never_blocks.py`
直接 **参数化自常量列表本身** —— 以后任何人往 BLOCKED_PATTERNS /
BLOCKED_FLAGS 加词，都自动继承「作为正文必须能发、作为命令必须被拦」这条
不变量，不会再出现「加个通用词把正文炸了」。这与 2026-04-21 删元字符
denylist 时留下的 `test_command_shell_chars_allowed.py` 是同一课的第二遍。

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
