---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/tooling/dispatcher.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

`execute` 重新组装 ToolResult 时透传 `images`，漏掉就会在分发层把 MCP 图片静默丢掉。

## 2026-09-03（批 2a.5）— `model_tools()` = visible − deferred

`visible_tools()` 语义不变（search / execute / spec_for 都看它）；新 `model_tools()` 是交给模型的 up-front
清单，扣掉 deferred。插件工具默认只经 tool_search 可达就是这一行的效果；无 deferred 时两者相等。

## 2026-08-13（管线审后）— search_lines 封顶不可绕过

三个绕过口全堵（管线审 I#3）：①空白查询先算 tokens、空即走 overview 分支（不再
vacuous 全匹配）；②单 token 查询同受封顶；③工具行与 card 行**分类封顶**（`_SEARCH_MAX_HITS=12` + `_SEARCH_MAX_CARD_HITS=4`，
二审：共享预算会让 12 条工具行饿死整个能力索引）。④打分是**元组**且**只用内容词**（四审：`i`/`to` 子串命中任意名字，最高位被噪音决定
——`speak` 名字无 `i` 反被 30 个带 `i` 的无关工具挤出）：打分 token=长度>2 且不在
`_GLUE_TOKENS` 停用词表（纯胶水查询退回全量）；**过滤语义（ALL 池 / all_matched）
仍用完整 token**——这条边界别混。层级：叶子名命中（`rsplit('__',1)[-1]`，前缀
`mcp__模块__` 不给整模块白送分）> 覆盖率（上界=token 数）> 出现次数（tiebreak，
ALL 路径靠它排序）；token 用 dict.fromkeys 去重保序（排序可复现，别换 set）。
⑤**expressive 保底席**（五审重做）：判据=`annotations.expressive` **or 注入的
`is_expressive` 活回调**——生产回复工具全是 MCP spec，注解带不了这个字段（mcp_channel
只映射 readOnly/destructive），真源是 TurnOptions.expressive_tools 名单；assembly 把
`ExpressionContract.is_expressive` 传进 ToolDispatcher（**活对象不许快照**：expansion
轮内会 add_tools）。席位语义=**保证在场不动序**：已排进前 12 的不动位置；没进的最多
3 个从尾部替换最弱的**非 expressive** 席（「截断砍最弱」不变量保持）；入席门槛=过滤
命中（内容词覆盖≥1），无命中不搭车——门槛故意宽，因为不再置顶、只花尾部席位。补席**倒序放置**（最强的最后放→占最靠前的空出尾席），多个补席工具间保持排名序；非 expressive 席耗尽时多余 missing 不再补、logger.debug 留痕（六审 M#2/M#4）。
同款先例=marker_tools 的双判据（annotation or 注入名单）。_GLUE_TOKENS 只留长度门槛
（>2 字符）管不到的词，≤2 字符胶水由门槛自身兜。card 行也按覆盖率排名后截 4（展示文本按行打分，不套 _ranked）。
`all_matched` 语义固定=ALL 过滤非空（它决定 card 的 ALL/ANY 噪音策略，别换判据）。overview 分支刻意不封顶——那就是全量清单请求。
`_hay` 预构建成 dict 复用（原 per-token 重拼）。

## 2026-08-13 — tool_search 多词查询分词匹配

整串子串匹配让多词验证探针（`tool_search("narra reply speak send")`）对在册工具返回
"(no matches)"，模型据此判定回复工具不存在而沉默（8/13 语音对抗实测）。
`search_lines` 改分词：全 token AND 命中优先；AND 空且多词时降级 **ANY token 兜底
——按 token 命中数排名并封顶 `_FALLBACK_MAX_HITS=12`**（胶水词探针不许把整个
工具面灌进 turn，二轮 review Important #4）。card_index 行过滤**镜像产生 hits 的
模式**（AND 命中就 AND，兜底才 ANY）——精确查询不再被松散卡片噪音尾随。
单词查询行为不变。测试锁
tests/nexus_power/test_tooling.py::test_search_lines_multi_word_query_tokenizes。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)(配套:C2 落地)

`visible_tools` 删除每通道 `sorted(name)`——排序会把中途展开的新工具**插进
MCP 段中间**,把插入点之后的字节全部挤出 provider 缓存前缀,正好违反本文件
自己承诺的「expansion appends, never resorts」。现顺序=(通道序,注册序);
注册序确定性由通道自己保证(见 [[mcp_channel]] 同日条目)。工具数组初始顺序
因此一次性变化(旧用户首轮 cache miss 一次,之后照常)。

## 2026-07-30 — 派发前 required 校验(hermes 形)

policy/spec/marker 之后、路由之前,按 spec.input_schema.required 查缺:缺字段
不进 handler,错误里带缺失清单+完整 schema 让模型重发。handler 自己的缺省回退
曾把「参数没到」翻译成误导性错误(write 无 path→workspace 根→Is a directory)。
对 MCP/skills 等所有通道生效(schema 同源)。

# tooling/dispatcher — 唯一分发器

代数缓存(通道 generation 元组)、disallowed/allowlist 过滤在 schema 层生效(A1)、标签工具裁决后短路、search_lines=自研模型无关 tool_search 底座。「模型可见≡实际注册」由本类单点保证。
