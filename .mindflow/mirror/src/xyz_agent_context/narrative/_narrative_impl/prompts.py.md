---
code_file: src/xyz_agent_context/narrative/_narrative_impl/prompts.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — R4d：Created At 迁入 turn 版（created_at 有两个时钟源）

R4c 给时间戳做了唯一规范化渲染（`_canonical_timestamp`），修的是**格式**；
R4d 修的是**取值来源**——这是两个不同问题，前者不能覆盖后者：

- `NarrativeRepository._entity_to_row()` **不写** created_at/updated_at，
  INSERT 走 schema 默认 `(datetime('now'))`，取的是 **DB 时钟**（秒级）。
- `_narrative_impl/crud.py` 里 `now = datetime.now(timezone.utc)` 在两次
  proxy 往返 + save **之前**捕获，构造的内存对象取的是 **Python 时钟**（带微秒）。
- 于是**创建 narrative 的那一轮**渲染出的秒数，可能与之后每一轮从 DB 回读
  渲染出的秒数不同（只要那两次往返跨了秒边界）。规范化后两者都是 23 字节，
  差异是**等长替换**——`[SYSPROMPT-BREAKDOWN]` 的 per-part 字节数看不见，
  但缓存前缀在此处（模板内约 1051 字节偏移，E2 标记的断点区）被打穿。

修法选择（两条路，选了前者）：

1. **（采用）把 `- Created At: {created_at}` 从 STABLE 模板迁到 TURN 模板。**
   稳定半从此**不含任何时间戳**，"哪个时钟"这个问题对缓存不再有意义；
   prompt 表面只动一行；不改数据层，无 DB 行为风险。铁律 #16：迁移不是丢弃，
   模型每轮仍在 turn 块看到创建时间（`- Created: ...`）。
2. （未采用）让 `_entity_to_row` 显式写入 Python 侧 created_at/updated_at。
   它修的是数据层不一致（本身也是真问题），但**缓存前缀里仍留着一个时间戳**——
   任何未来的第二个写入路径、时区/精度差异、乃至 MySQL `DATETIME(6)` 与
   SQLite TEXT 的回读差异都会再打穿一次；而且改 INSERT 语义触碰铁律 #6 的
   谨慎区（entity.created_at 为 None 时会写空）。故记为已知问题而非本次修法。

至此 STABLE 模板的四条易变行全部迁出：`Updated At`（R4a）、`Name`（R4c）、
`Current Summary`（R4a）、`Created At`（R4d）。留下的 description + actors
在 CLI session 生命周期内恒定（actor 变更是结构性成员事件，属合法一次性打穿）。
**改共享文案仍必须同时改 MAIN 与 STABLE 两处**，等价性（稳定版 = 完整版减去且
仅减去这四行）由 `tests/narrative/test_narrative_prompt_split.py` 锁定。

## 2026-07-28 — R4c：Name 迁入 turn 版（E2 实证 Name 是 LLM 每轮可变字段）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

E2 实验（`reference/self_notebook/specs/2026-07-25-e2-request-capture-findings.md`
§3）逐字节比对证明 narrative Name 在轮间从"query 截断草稿名"漂移到 LLM 定稿名，
是 system[2] 前缀 ~1.2K 处的断点之一。**判定依据**：updater.py:386 每次 LLM
update 都重写 `narrative_info.name`（与 current_summary 同源、同频），它没有
可用的"canonical 稳定形态"——所以选**迁出稳定块**而非规范化渲染。STABLE 模板的
"Narrative Details" 只剩 Description（updater 从不改 description）；TURN 模板
现承载 name + updated_at + current_summary 三个字段。actors 留稳定块（结构性
成员变更 = 合法一次性打穿）。MAIN 模板（开关关的 legacy 路径）不动结构。

## 2026-07-28 — R4a：NARRATIVE_MAIN_PROMPT_TEMPLATE 拆出稳定版 + turn 版

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

新增 `NARRATIVE_STABLE_PROMPT_TEMPLATE`（= MAIN 模板逐字节减去
`- Updated At: {updated_at}` 与 `- Current Summary: {current_summary}` 两行）与
`NARRATIVE_TURN_PROMPT_TEMPLATE`（`## Current narrative state` 块，恰好承载这两
个每轮易变字段）。MAIN 模板**原样保留**——relocation 开关（见
[[context_runtime.py]]）关闭时走它，保证字节级回滚。**改共享文案必须同时改 MAIN
与 STABLE 两处**，等价性由 `tests/narrative/test_narrative_prompt_split.py` 锁定。

# prompts.py — narrative 子系统的全部 prompt 常量

## 为什么存在

narrative 子系统有多个 LLM 消费面：主 system prompt 的 Narrative 段
（PromptBuilder）、continuity 判定（ContinuityDetector）、检索仲裁
（NarrativeRetrieval 的 single/unified match）、元数据增量更新
（NarrativeUpdater）。所有 prompt 文本集中在这一个文件，逻辑代码零内联字符串，
prompt 审计只看这里。

## 上下游关系

**被谁用**：[[prompt_builder.py]]（type/actor 描述常量 + 三个 NARRATIVE_*_
PROMPT_TEMPLATE）；`continuity.py`（CONTINUITY_DETECTION_INSTRUCTIONS）；
`_retrieval_llm.py`（NARRATIVE_SINGLE_MATCH / UNIFIED_MATCH 两版）；
`updater.py`（NARRATIVE_UPDATE_INSTRUCTIONS）。

**依赖谁**：无。零 import，纯常量文件。

## 设计决策

- 8 个特殊 default Narrative 的边界规则写死在 continuity/unified-match prompt
  里——它们是平台级通用分类，不是场景逻辑（铁律 #4）。
- NARRATIVE_UPDATE_INSTRUCTIONS 强制 current_summary 为结构化 fact-sheet
  （bullet、8-12 条上限）而非散文——它每轮被 LLM 重生成，是 system prompt 增长
  的头号来源（见 [SYSPROMPT-BREAKDOWN] 的 nar_summary_chars）。
- 模板占位符与 Narrative 字段一一对应，注释块里逐个列出——加占位符必须同步
  prompt_builder 的 format kwargs，缺一个就 KeyError。

## Gotcha / 边界情况

- 文件里有**两处**关于 "Narrative main system prompt template" 的注释块头
  （历史遗留，第一处在 :40-52 只有注释无常量），真正的模板在文件尾部。
- MAIN/STABLE 模板以 `"\n## Narrative System"` 开头（前导换行），与
  context_runtime 的 `"\n\n".join` 组合产生刻意的段间距——改前导空白会静默改变
  system prompt 字节。
