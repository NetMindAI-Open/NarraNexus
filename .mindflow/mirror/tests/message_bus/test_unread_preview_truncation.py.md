---
code_file: tests/message_bus/test_unread_preview_truncation.py
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — 为什么存在

钉住 B-23 / upstream #73：未读列表绝不给 agent 一条被静默截断的消息。用 #73 原文（三段、
约 560 字）断言整条渲染；超出 `UNREAD_PREVIEW_MAX_CHARS` 的行必须带截断声明（原长、展示长、
`read_history`），恰好等于预算的不标；分片行 `(part i/n)` 与截断声明共存；静态块那句
「未读已在 context」必须说明长消息会被截断。把 [[../../plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/message_bus_module]]
的渲染改回 `[:200]` 硬切，前四条变红。

## 2026-09-11 — review 跟进（PR#401）

新增：#73 原文断言按「首行 + `  > ` 引用续行」的渲染形式整条出现（核心资产保留、改形式）；正文伪造一行他人 row / 伪造表头 / 伪造 cut 标记——列表里只有两条真 row，伪造行全是引用行；cut 标记指路给确切调用（DM→`with_agent`、团队房→`team_id`、无句柄时如实说取不到）；总预算保留最新行、声明被略去的条数并列出对应调用；预算内无省略声明；最新一条单独超预算也照常展示。「无标记」断言改为直接查 `UNREAD_CUT_MARKER` 标记行，不再拿 `read_history` 当代理。回退证明：`body_lines`（原 `_unread_body`）改回原样返回 → 两条结构用例红；总预算置无穷 → 预算用例红；去掉 team_id 分支 → 团队房指路用例红。

## 2026-09-11 — review 二轮跟进（PR#401）

新增：房名含换行+伪造 row 时，未读列表与 `### Your teams` 里 `- \`` 开头的行仍只有真行；Known Agents 的名字/描述同理；窗口外（total=50、窗口 20）按 `total - shown` 声明 30 条未展示；「最新一条单独超预算」用 monkeypatch 把 `UNREAD_SPAN_MAX_CHARS` 压到 200 真正构造该分支（并验证更旧一行被让出）；总预算断言把声明行一并计入；静态块断言两类例外（shown cut / not shown）都在同一句。回退证明：`_bus_tag` 的 label 改回 `.strip()` → 未读房名用例红；去掉「最新一条必留」护栏 → 超预算用例红；整文件回退 → 7 条红。

## 2026-09-11 — review 三轮跟进（PR#401）

二轮三条「cannot_forge」用例的正向期望里写着幸存的伪造串，已删除，改为按语法的性质测试：`NASTY` 覆盖三种行语法的每个分隔符（换行、反引号及全角反引号、` · `、`[`/`]`、` — `、`: `、`(teammate)`、编码本身的 `"` 与 `\`）、组合、`·`/`•` 等形近字、控制字符与截断点上的反引号；对未读行、`### Your teams`、Known Agents 分别用严格正则 fullmatch 整行，并断言标签 `json.loads` 回来恰好等于独立推导的规范化文本、发送者与正文是真值、`(teammate)` 不能被伪造。另有：伪造 row 只作为引号内文本存在的整行期望；超长标签以 `…` 标记且长度等于上限、超长 agent/team id 原样完整出现；`_not_shown_line` 直接单测（让出行的调用新到旧、窗口外单独说明、无句柄如实说）；短行不连锁让出（15 条短私信 + 5 条长房消息，至少保留 10 行）。回退证明：`_inline_field` 去掉 JSON 引号 → 5 条语法用例红；`NOT_SHOWN_MAX_CALLS` 放到 99 → 连锁用例红（只剩 4 行）。

2026-09-11：窗口外条数用例的期望改为「all N are older than this list」（`omitted` 为空时不再说「those」）。
