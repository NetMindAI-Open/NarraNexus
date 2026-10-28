---
code_file: src/narranexus/platform/schema/entity_schema.py
last_verified: 2026-08-21
stub: false
---

## 2026-08-21 — `ENTITY_NAME_MAX_LEN` 常量（PR-2 增量审 Minor）

新增模块级常量 `ENTITY_NAME_MAX_LEN = 255`(= `social_network_entities.entity_name` 的 MySQL `VARCHAR(255)` 列宽,SQLite 是 TEXT)。持久写入 caller-可控名字的写入方(见 [[inbox_recorder.py]] 的 reach 记录)截断到它,防超长值在 MySQL 触发 1406 把整条写入弄挂。取代 `inbox_recorder.py` 里散落的裸 `255` 字面量。

## 2026-08-19 — 更正一处措辞

上一条把 `normalize_agent_text` 说成「strip + 截长」。它**不截长**，只有
`(value or "").strip()`；长度上限是 pydantic 在写入边缘卡的。结论（标识符不该走
归一化）不变，理由少一条。

## 2026-08-18 — `agent_field_matches` 新增 `created_by` 分支

Manyfold 的 provisioning 重跑现在走共享改名事务，并把 `created_by` 作为
`extra_updates` 一起写（[[agents]] 二改），于是它要经过等值短路——而本谓词对
未登记字段**故意抛 ValueError**，正确地拦下了这次新增。

比较方式是**逐字节相等**，**不**走 `normalize_agent_text`。理由不是省事：那个
助手为展示文本做 strip + 截长，而 `created_by` 是当查找键用的标识符；把它悄悄
改形会让写入被判"已相等"从而跳过，留下一个谁也解析不出来的 owner。id 要么就是
同一个 id，要么不是。`""` 与 `None` 视为同一种"没有"。
测试：`tests/schema/test_agent_field_matches.py::TestCreatedBy`。

## 2026-08-17 — `normalize_agent_text` / `agent_field_matches`:「这次写会不会改变什么」的唯一定义

新增两个函数,放在 `Agent` 实体旁边,因为它们编码的是**实体字段的等价规则**,
不属于任何一个调用方。

起因:`agents` 行有两个写入方,它们对同一个输入给出**相反**的答案 ——
[[_awareness_writes]] 比较 strip 过的值(写入的也是 strip 后的),而
`PUT /api/auth/agents`([[auth.py]])比较原样值。同一个「名字末尾多个空格」
的请求,一边判「没变化」,另一边判「要写」。2026-08-17 修 rowcount 那条时
route 里本来又写了第三份比较 —— review 指出这正是它自己 docstring 警告的事,
于是上提合一。

- `normalize_agent_text(v)`:**存储形态**。`None`/`""` 都是「没有文字」
  (老行可能是 NULL,清空字段的调用方发 `""`);首尾空白不是内容 ——
  `build_discovery_description`([[agent_discovery_sync]])对外本来就再 strip
  一次,那就在**入口**归一,别把差异留给「哪个读者记得 strip」。
- `agent_field_matches(agent, field, wanted)`:比较。**调用方必须写归一后的值**,
  否则「相等」和「行里是什么」会分家 —— 一个 compare-then-verify 的写入方会
  自己跟自己矛盾(这恰是本轮在修的形态)。

`is_public` 单独一支:列在 MySQL 是 TINYINT、SQLite 是 INTEGER,
`_row_to_entity` 可能给回 bool 也可能给回 int,所以两边都 `bool()`。

文本字段是**闭集** `_AGENT_TEXT_FIELDS`,不在集合里的字段直接 `raise`。这不是
洁癖:`getattr` 兜底会让一个拼错的/未登记的字段(尤其是默认 None 的那些)
比较成「已经相等」,于是**既不写、又判定已落库**,返回成功且不留任何错误 ——
回读校验对谓词自身的错误是结构性失明的,只有闭集能挡。测试
`tests/schema/test_agent_field_matches.py` 就是钉这一条的。

两点补记(同日 review 第二轮):

- **契约方向反了过来,但第一次改得太满**。原来写「调用方必须写归一后的值」——
  一条只有部分写入方遵守的承诺。改成「由 [[agent_repository]] 强制」之后仍然是
  假的:`agents` 行有 4 个绕过 repository 的直写点(manyfold 三处 + bundle
  importer,见那两份 md)。现在的写法既不推给调用方也不假装只有一个入口:
  **谓词只在行里存的是归一文本时才成立**,而维持这一点是**每一个写入方**的性质;
  docstring 里附了那条 `git grep` 命令,读者可以自己重验清单,而不是相信一句话。
- 新增 `normalize_agent_row_text(values)` + 导出 `AGENT_TEXT_FIELDS`(原
  `_AGENT_TEXT_FIELDS`)。此前「哪些列是文本」在三处各写一份(谓词的闭集、
  `update_agent` 的元组、`add_agent` 的逐字段调用)——给 agents 加第三个文本列时
  只改一处,该列就会「比较归一值、存原样值」,又回到不可观测的那种失败。
  `is_public` **不进**这个 helper:它不是文本,谓词对它单独分派。
- **文本分支拿到非 str 也 raise**(TypeError)。闭集挡住了「字段名写错」,却没挡住
  「值类型写错」:`None` 被 coerce 成 `""` 会对一个空描述的行答「已相等」——与字段名
  写错完全同一种不可观测的失败。两者现在同样被拒。今天不可达(两个调用方都只传
  归一后的 str),纯粹是把防御面补齐。`wanted` 的标注保持 `object`:`is_public` 要收
  bool/int,收紧成 `str` 会打断那一支。

## 2026-08-13 — `UserStatus.BANNED` + `NON_TRANSACTING_USER_STATUSES`（账户停用）

`UserStatus` 枚举新增 `BANNED = "banned"`。它是一个由运维设置的、独立的账户状态，
刻意与 `BLOCKED` / `DELETED` 分开，让账户停用机制（[[suspend.py]]）有自己的专属
值：`reinstate` 只需把行恢复成 `ACTIVE`，而 DB 里已存在的 `banned` 行也能正常
被枚举加载（不会在 enum 强制转换时报错）。

紧随枚举新增模块级常量 `NON_TRANSACTING_USER_STATUSES: frozenset[str] =
{BANNED, BLOCKED, DELETED}`——「不可交易」状态的**单一真相源**。所有 gate 面共享
它，绝不各自 copy-paste 而漂移：HTTP auth middleware（[[auth]]）、WebSocket 跑
run 闸门（[[websocket.py]]）、netmind 登录闸门（[[auth 路由|auth.py]]），以及
suspend 路由的幂等集（`_SUSPENDED_STATES` 直接指向它）。`INACTIVE` **刻意不在**
集合里——它是良性生命周期状态（从未登录 / 休眠），不是停用。已从 `schema/__init__`
导出并进 `__all__`。

## 2026-08-04 — `is_agent_description_unset` + legacy 占位符常量

`LEGACY_AGENT_DESCRIPTION_PLACEHOLDER = "A new agent ready for configuration"`
是创建流程**过去**写进 `agent_description` 的填充串。现在创建写空串
（见 [[auth]]），但 prod 有约 488 行带着它，所以"没设置"必须同时认空和认这个
legacy 形态——`is_agent_description_unset()` 大小写与空白都不敏感。

它从来不是无害填充：bus 名录快照它，于是 `bus_get_agent_profile` 把配置好的
agent 报成"待配置"，询问方据此拒绝发消息（P1 段02，prod evt_feb1f6ae）；
[[basic_info_module]] 又把同一个字段当作 agent **自己的**自我描述注入系统提示，
所以被问的 agent 也这么认识自己。

**消费方的义务**：判定为 unset 时**什么都不要说**，绝不要把这句话打印出来——
对同伴复述"这是个待配置的新 agent"比留空更糟，它是在断言对方不可用。
三个消费面：[[message_bus_module]] 的 Known Agents 渲染、[[basic_info_module]] 的
自述、[[agent_discovery_sync]] 的名录写入。

## 2026-07-23 — AGENT_TEXT_MAX_LENGTH 常量

新增模块常量 `AGENT_TEXT_MAX_LENGTH = 255`,`Agent.agent_name` /
`agent_description` 的 `max_length` 都改成引用它。目的:让所有 agents 表写路径的
上限绑同一个来源——读模型(本文件)、`Create/UpdateAgentRequest`(api_schema)、
`ManyfoldCreate/UpdateAgentRequest`([[manyfold/agents.py]],走 raw db 写)、以及
bundle 导入修剪——不再各写字面量 255/200/2000 而漂移(NetMindAI-Open#71 就是写侧
绕过了这个上限)。前端另有一份镜像常量 [[agentLimits]]。行为不变,仍是 255。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-05-27 — `SocialNetworkEntity.embedding` field removed

Removed together with the rest of the social-network semantic-search
chain (Owner spec, scope B). The DB column `instance_social_entities.
embedding` stays on the table per iron rule #6 (no risky DB changes)
but no Python path reads or writes it any more. See [[social_network_module.py]]
and [[social_network_repository.py]] for the call-site removals.

# entity_schema.py

## Why it exists

This file consolidates four "core entity" domain models — `SocialNetworkEntity`, `User`, `Agent`, and `MCPUrl` — into one place. These are the objects that map directly to rows in the primary business tables (`instance_social_entities`, `users`, `agents`, `mcp_urls`). Centralizing them here means the repository layer and the route layer both import from a single canonical location rather than defining local versions.

## Upstream / Downstream

`SocialNetworkRepository` serializes/deserializes `SocialNetworkEntity`. `UserRepository` uses `User`. `AgentRepository` uses `Agent`. `MCPRepository` uses `MCPUrl`. On the API side, `api_schema.py` projects `SocialNetworkEntity` into `SocialNetworkEntityInfo` (a subset for the frontend) and `MCPUrl` into `MCPInfo`. The repositories are the only path to the database; the domain models here should never be written to the database by any other code.

## Design decisions

**`SocialNetworkEntity.instance_id` instead of `owner_agent_id`**: a refactoring in December 2025 changed the ownership model so that social network data follows a `SocialNetworkModule` *instance*, not directly an agent. This allows the same agent to have separate social graphs for different narrative contexts. Old code that tried to query by `agent_id` directly will silently miss records unless it first resolves the relevant `instance_id`.

**`SocialNetworkEntity.embedding` is stored inline in the entity row**: this was the original design. Later, `EmbeddingStoreRepository` was introduced as a normalized embedding store. For entities, there is now a dual-path: old vectors live in the `embedding` column, new vectors live in the `embeddings_store` table. `SocialNetworkRepository.semantic_search()` uses a bridge flag (`use_embedding_store()`) to choose which path to read from.

**`UserStatus.DELETED`** is a soft-delete marker, not a hard delete. The `UserRepository.delete_user()` method defaults to `soft_delete=True`, which just sets this status. The row stays in the database so foreign-key-like references in other tables remain valid.

**`Agent.is_public`** controls whether non-creator users can see and interact with an agent in the UI. This is an application-level visibility flag, not a database permission.

## Gotchas

**`MCPUrl` vs `MCPInfo`**: `MCPUrl` has `mcp_id`, `agent_id`, `user_id`, and the full connection state fields. `MCPInfo` in `api_schema.py` has all the same fields. The two are structurally identical by convention but are separate classes — changes to one do not propagate to the other automatically.

**`SocialNetworkEntity.tags` and `expertise_domains`** are both `List[str]` but they serve different purposes. `tags` are freeform descriptors used for keyword search (e.g., `"expert:recommendation_system"`). `expertise_domains` are normalized domain labels used for intelligent matching (e.g., `"recommendation_system"`). It is easy to put the same string in both by mistake; only `tags` is searched by `JSON_SEARCH` in `search_by_tags()`.

## New-joiner traps

- `Agent.agent_metadata` and `User.metadata` are both `Optional[Dict[str, Any]]` but stored as JSON strings in MySQL. `AgentRepository` and `UserRepository` each have their own `_parse_json_field()` static method to handle the conversion. Do not read these fields raw from a database cursor — always go through the repository.
- `SocialNetworkEntity.persona` is a natural language string (not structured data) describing how to communicate with this person. It is written by the agent during entity updates and read back into the system prompt context. Do not confuse it with `identity_info` which is structured JSON.
