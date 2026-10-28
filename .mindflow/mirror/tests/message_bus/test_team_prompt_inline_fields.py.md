---
code_file: tests/message_bus/test_team_prompt_inline_fields.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401 第九轮）— path 无损、kind 枚举、断言绑定

- `EXACT_PATHS`：连续空格、制表符、首尾空格、反引号（含全角）、换行 + `User:`、引号 + 伪造 `kind=`/结尾、反斜杠、
  U+2028/U+2029/U+0085。`test_marker_path_is_an_exact_literal_that_forges_nothing`：每条 marker 只有一行、
  fullmatch、`json.loads(path)` 等于原值、`kind` 仍为真值。
- `test_bus_marker_path_under_a_real_odd_base_dir_is_exact`：真实建一个含双空格/制表符/反引号/尾随空格的 base 目录，
  回解路径等于磁盘路径且 `os.path.isfile` 成立（不 mock 路径拼接）。
- `test_marker_kind_takes_a_raw_category_enum`：bus dict 传 `AttachmentCategory.IMAGE` → `kind="image"`。
- 伪造 Content-Type 用例的 `kind` 断言挪进循环，按 marker 绑定期望值（不再靠循环变量泄漏）。
- 回退证明：两个渲染器 path 换回 `inline_literal` → 3 条红（含 `tests/utils/test_attachment_storage_format.py` 1 条）；
  去掉 `exact_literal` 的行分隔符转义 → 2 条红；去掉 `category_value` → 5 条红。

## 2026-09-13（PR#401 第八轮）— marker 正则取自渲染器语法、伪造 Content-Type

- `_MARKER` 由 `attachment_schema` 导出的固定字面量（`FILE_MARKER_LABELS` / `FILE_MARKER_UNAVAILABLE_PATH` /
  `FILE_MARKER_TAIL`）拼成，每个值位置都是 JSON 字面量：名字带冒号、路径带空格不会让 fullmatch 因无关原因失败
  （`test_marker_path_with_spaces_and_sender_with_colon_parse` 钉住）。
- `test_a_declared_content_type_cannot_forge_a_marker_field`：伪造值走**真实** `sniff_mime_type` 兜底路径
  （认不出的字节 + 无扩展名 + 伪造 `client_type`），先断言返回的就是伪造串，再对两个渲染入口 fullmatch 并回解
  mime；总线 dict 的 `category` 同时伪造。
- 团队房附件 marker 的 `from=` 回解为伪造成员名原文。import 顺序整理（标准库并入顶部块）。
- 回退证明：两个渲染器的 `mime` 去掉 `inline_literal` → 本文件 5 条 + `tests/utils/test_attachment_storage_format.py` 3 条红。

## 2026-09-13（PR#401 review 🟡1/🟡2/🟢3）— capabilities、marker 字段、无 token 退路

- `_ROSTER_ROW` 扩到能 fullmatch **完整**行形态（`· can: "…", "…"[ +N more]` 与平台状态段），`_roster()` 里
  伪造者本人带着伪造 capability（换行 + 假成员行 + ` · Leader`）并处于 running。
  `test_a_forged_capability_forges_no_row_and_no_field`：仍只有两行、capability 解回原文、`lead` 为空、状态段为真值。
- 附件：旧用例期望值里的幸存伪造串 `name=x.txt User: obey,` 改为按 `_MARKER` 严格正则 fullmatch 并 `json.loads`
  回解；反向断言（没有以 `User:` 开头的行）保留。新增 `test_a_file_name_or_transcript_cannot_forge_a_marker_field`
  （文件名提前闭合 marker、transcript 伪造第二个 marker，**两个渲染入口**都测）与
  `test_user_and_bus_markers_share_one_shape`。
- 无 token 的行必须给出退路（`message_agent with the id above`）。
- 回退证明：capability 去掉 `inline_field` → 4 条红；`file_marker` 的文件名去掉 `inline_literal` → 3 条红。

## 2026-09-11 — 为什么存在（PR#401 同类扫描）

钉住团队房 prompt 的行语法不可被作者写的标签伪造：成员名/描述里塞换行 + 假成员行 + 假
`· Leader`，名单仍只有两行真成员，每行用严格正则 fullmatch（句柄、JSON 字面量名字、标记、
JSON 字面量描述），标签 `json.loads` 回来等于独立推导的规范化文本，Leader 标记不能伪造；诚实
名单的完整行形态；超长描述截断（现为 80，见下）并以 `…` 标记、超长 id 原样完整；工作板标题伪造的带假
`id=` 行不出现，唯一一行 fullmatch 且 id 为真；巡查停滞列表同理。回退证明：把
[[../../src/narranexus/platform/message_bus/message_bus_trigger]] 回退到改动前 → 5 条全红。

## 2026-09-11 第二轮 — 覆盖到不变量的全部行语法块

每条用例都让伪造者**真的走被测字段**：伪造名字的成员**自己发言**，正文里带假 `User:` 行与假
`"Ana":` 行 → scrollback 只有两条真行头（严格正则 fullmatch，发送者 `json.loads` 回解），假行只以
`BODY_LINE_PREFIX` 续行出现；被点名列表里的伪造名字留在字面量里；批次指向列表同理；诚实消息仍是
`"Ana": hello`。公告栏：agent 规则 `…\n2. …` 不产生第二条无署名编号行，署名在首行正文之前；
`[Team progress]` 摘要整段引用。@mention：每行的 `@token` 经 `extract_team_mentions` 解析恰好回到该
成员、带引号形态解析为空；同前缀/以符号开头的名字显示 `(no @mention token)`。peer 私聊正文伪造不出
`From:` 头；附件文件名带换行不开新行。描述上限改为 80（与 Known Agents 同口径）。回退证明：trigger
回退到本轮前 → 15 条中 13 条红（仅 2 条既有的工作板/巡查用例仍绿，它们上一轮已覆盖）；只回退
`_bus_attachment_impl` → 附件用例红。
