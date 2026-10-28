---
code_file: src/narranexus/platform/utils/inline_field.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401 review 🟡2/🟢4）— 迁到 `platform.utils`，新增 `inline_literal`

文件从 `platform/message_bus/` 迁到 `platform/utils/`：附件 marker 的唯一渲染函数
`attachment_schema.file_marker` 也要用这个编码器，而 schema 层不能 import 总线包（`message_bus/__init__`
会拉起 trigger/service）。utils 在 trigger、插件、schema 之下，三方共用同一份定义。

新增 `inline_literal(value)`：与 `inline_field` 标签形态相同的 JSON 字面量（折叠空白、换反引号），但
**不截断**——用于读者需要完整内容的作者文本（文件名、语音转写），截断会丢内容。`inline_field` 的标签
分支与它共用 `_flatten`。`body_lines` 的 docstring 改为 raw string，`\r` / `\u2028` 不再被编译成真的
控制字符。

## 2026-09-13（PR#401 review 第九轮 🟡1）— 新增 `exact_literal`：句柄的无损字面量

`inline_literal` 是给**标签**用的（折叠空白、剥首尾、反引号换 `'`），不能用于 agent 原样喂给 Read 的**路径**：
部署方把 `BASE_WORKING_PATH` 配成含连续空格 / 制表符 / 尾随空格 / 反引号的目录时，折叠后的路径打不开。
`exact_literal(value)` = `json.dumps(str(value), ensure_ascii=False)`，再把 JSON 不转义、但 `splitlines()`
认作换行的 U+0085 / U+2028 / U+2029 手工转义：`json.loads` 回解逐字节等于原值，同时引号、反斜杠、
所有控制字符都被转义在引号内，既不能提前闭合字段也不能另起一行。反引号不替换——使用它的 marker 行与
附件列表行都不在 code span 里。三个编码器的分工：`inline_field` = 可截断标签，`inline_literal` = 不截断标签，
`exact_literal` = 需要加引号的精确句柄（目前只有文件路径）。

# inline_field — 行语法 prompt 块里作者可写文本的唯一编码器

## 为什么存在

好几个 prompt 块是「行语法」：插件 [[../../../../plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/message_bus_module]]
的未读列表 / Known Agents / Your teams，以及 [[../message_bus/message_bus_trigger]] 的团队房成员名单、工作板、
巡查停滞列表、公告栏署名。每行混着两种东西：agent 要原样抄进工具调用的**句柄**（agent/team/item id），
和 agent 或 owner 自己写的**标签**（名字、描述、标题）。标签里一个换行、反引号或该行语法的分隔符
就能伪造一行或一个字段。逐个字符类补洞永远补不完，所以按构造保证不可伪造。

编码器放在平台层，平台 trigger 与插件共用同一份定义（平台不能 import 插件）。2026-09-11 从插件
`_inline_field` 迁出（PR#401）。

## `inline_field(value, max_chars=INLINE_FIELD_MAX_CHARS)`

- **标签**（`max_chars` 为整数）：空白串折成一个空格 → 反引号（含全角 `｀`）换成 `'` → 超长按
  上限截断并以 `INLINE_FIELD_CUT_MARK`（`…`）结尾 → 以 JSON 字符串字面量输出
  （`json.dumps(..., ensure_ascii=False)`）。作者写的一切都在一对引号里，`"`/`\` 被转义，字段无法
  提前结束或另起一行，字面量能原样解码回展示文本。
- **句柄**（`max_chars=None`）：永不截断、不加引号，只折叠空白并替换反引号，不能逃出 code span 或行。

常量：`INLINE_FIELD_MAX_CHARS = 120`（名字/标题），`INLINE_DESCRIPTION_MAX_CHARS = 80`
（Known Agents 与团队房 roster 的描述，两面同口径）。确定性函数，prompt 字节稳定。

## `body_lines(text)` / `quoted_block(text)` — 多行正文

消息与规则的正文可以合法多行，不能折成一行，所以按行布局：`body_lines` 首行接在行头后，之后每行
以 `BODY_LINE_PREFIX`（`"  > "`）引用；`quoted_block` 连首行一起引用（用于没有独立行头、挂在表头下的
整段）。任何行语法块的行都不以该前缀开头，所以正文里的 `User: …`、`- [open] …`、`2. …` 只能读作
所属行的续行。按 `splitlines` 切分（含 `\r`、`\u2028` 等），空续行保留裸 `>`。2026-09-11 从插件
`_unread_body` 迁出，插件未读列表与平台 trigger 共用。使用处的完整清单见 [[../message_bus/message_bus_trigger]] 的
「不变量的覆盖范围」表。
