---
code_file: src/narranexus/platform/module_system/data_access/store.py
stub: false
last_verified: 2026-09-04
---

## 2026-08-19 — `update_awareness` 现在先读后写：保住平台身份记录

这个工具把**整份 profile** 交给模型重写并原样收回，而它规定的格式只列 4 节，
**不含** `## Identity Changes (platform record)`——同时 §5 和它自己的 When to
Update 都在鼓励模型经常重写。于是改名事务刚写好的身份更正，撑到模型下一次整理
profile 就整段没了：agent 回到"自述旧名、无任何平台更正"，而且这个状态连修都检测
不到（对账正是靠那条记录或自述行发现不一致的）。

改成先 `get_by_instance` 读旧值，用 [[_awareness_writes]] 的
`carry_over_platform_record` 把那一段接回去。**不是**在提示词里请模型保留它——
铁律 #15：机器可知的事实要推导，提示词是补充不是机制。

⚠ 云端那一半在 [[awareness]] 路由里（`PUT /agents/{id}/awareness`）。第一版只改了
本地 DirectStore，**而事故发生在 prod**——两条路径只修一条，是这次改动反复犯的那个错。

连带：`tests/module/test_data_access.py` 与 `tests/backend/test_awareness_create_missing.py`
的假 repo 要实现 `get_by_instance`，因为这个接缝现在真的会读。

## 2026-08-18(补)— 判断改调共享规则

`DirectStore.create_agent` 的空名/长度判改调 [[social_network_module.py]] 的
`create_agent_text_reject`,兜底串改调 `default_created_by_description` ——
与 Http 孪生**同一个函数**,不再是两份逐字复制的代码 + 两句「保持一致」的注释。
理由见那份 md。`AGENT_TEXT_MAX_LENGTH` 的深引随之不再需要。

## 2026-08-18 — 长度也由两条腿共用一处判,兜底串走截断

`DirectStore.create_agent` 在空名检查之后加同一顺序的长度检查
(`CREATE_AGENT_TEXT_TOO_LONG_MSG`,共享串),与 [[social_network.py]] 逐字同形。

为什么长度不放在 Http 那边的请求模型上:那会让云端在 handler 之前 422,同一次
工具调用云端读到 `invalid arguments (…)`、本地读到 pydantic 串 —— 详见
[[social_network.py]] 2026-08-18 修正条。顺序也是契约:归一 → 空名 → 长度,
反了 `" " * 300` 会被报成超长而不是空名。

描述为空时的兜底串 `f"Agent created by {name}"` 改为**截断**到
`AGENT_TEXT_MAX_LENGTH`:它是这条路径上唯一在所有检查之后才拼出来的值
(17 + 创建者名字),而拒绝一个调用方从没输入过的串没法解释。

## 2026-08-17 — create_agent 归一名字/描述,并拒绝空名

`DirectStore.create_agent` 在 owner 校验之后、`provision_new_agent` 之前:
`normalize_agent_text` 名字与描述,空名回 `CREATE_AGENT_EMPTY_NAME_MSG`
(共享串,见 [[social_network_module.py]])。

三个理由,都不是洁癖:
1. 行是归一后存的([[agent_repository]]),不先归一的话回执里 echo 给 agent 的
   名字与库里那行**不同形** —— 又一个"一边说存好了一边不一致"。
2. `agent_description or f"Agent created by …"` 这个兜底必须在 strip **之后**判,
   否则 `"  "` 绕过兜底存成空描述。
3. 空名在改名路径一直被拒,创建侧收 —— 同一个值可以创建、不可以改成。

[[social_network.py]] 路由那半边逐行同构(byte-parity 孪生),两边一起改。

## 2026-08-11 — job_create / job_pause / job_cancel 迁入 seam（job 写收尾）

Protocol+DirectStore+HttpStore 加三写：`job_create(agent_id, fields)`、`job_pause(agent_id, job_id)`、
`job_cancel(agent_id, job_id)`。DirectStore 委托 [[_job_writes]] 的 `create_job_from_args`/
`pause_job_from_args`/`cancel_job_from_args`（backend agent-scoped 路由 [[jobs]] 调同一函数 → byte-parity）。
create 失败键 `error`（同读，不走 `_write_message_key`）；pause/cancel 失败键 `message`(+job_id)，走
`_write_message_key` 把传输降级折回 message。pause/cancel 是 **bodyless PUT**，新增 `_put_dict` helper
（同 `_post_dict` 的 None→降级 + `_parse_dict`，只是 PUT 无 body）。**三写迁完 → job mcp 弃 db 凭据**，
配合 lark/narra 零凭据，mcp 容器可 strip `DB_PASSWORD`。

## 2026-08-10 (PR-11) — grep_memory 迁入 seam（memory 收尾）

Protocol+DirectStore+HttpStore 加 `grep_memory(agent_id, pattern, regex, limit) -> dict`。DirectStore 复刻 remember（MemoryCoordinator/MemoryEngine，方言安全）；HttpStore GET 现有路由 `/memory/grep`（regex 序列化成小写供 FastAPI bool 解析）。grep 有**自己**的输入契约：`_grep_reject`(pattern 1-256) + `_clamp_grep_limit`(1-200)，**不同于** remember 的 512/100——两 store 都跑保 parity。失败键 `matches`。ReDoS 安全在 [[retrieval]] grep_filter（regex 包+timeout），故 HTTP 侧不再拒 regex。**三工具全迁完 → general_memory mcp 弃 db 凭据**。 DirectStore/route 现返回 `{...,"truncated":bool}`（[[coordinator]] 的截断信号透出）。

## 2026-08-10 (PR-10) — get_chat_history 迁入 seam

Protocol+DirectStore+HttpStore 加 `get_chat_history(agent_id, instance_id, limit) -> dict`。DirectStore 调 [[chat_module]] 包导出的 `fetch_chat_history`（de-raw+instance-scope，闭 IDOR）；孪生路由 [[chat_history]] `POST /chat-history/by-instance` 调同一函数。instance_id 走 body 非 path（无需 _seg）；失败降级用工具 dict 形状（instance_id/total_messages/messages）。 DirectStore 外层 try 只守 `_db()`（懒建 MySQL 池会抛）→ 保 never-raises 不变式，与孪生路由包 get_db_client 同因（管线 fix-first 补）。

## 2026-08-10 (PR-9) — update_agent_profile 迁入 seam

Protocol + DirectStore + HttpStore 加 `update_agent_profile(agent_id, new_name, new_description) -> str`。
DirectStore 委托 [[_awareness_writes]] `update_agent_profile_from_args`；孪生路由 [[profile]] 调同一函数。
**首个 str-return 且动态串的方法**：不像 update_awareness 那样重建定值常量，而是路由回 `{"message": <串>}`、
HttpStore 拆信封原样返回（2xx）；传输降级（unreachable/≥400/非 JSON/无 message 键）一律兜成 "Error: ..." 串，
绝不返回 dict 或抛（DirectStore 只返串，parity）。

模块 docstring 的 parity 不变量补了 str-return 例外：`update_agent_profile` 的唯一输入上限（AGENT_TEXT_MAX_LENGTH name/desc cap）在共享 fn 里 enforce、路由 body **故意无 Field**——str 返回没有 _parse_dict 把 422 折回工具形状，路由级 422 会让 Http 侧降级成不同串、破 parity。故 str-return 的 bound 只能放共享 fn。

## 2026-08-10 (PR-8b) — job_update 迁入 seam

DirectStore.job_update(agent_id, job_id, fields) 委托 [[job_module]] 包导出的
`update_job_from_args`（[[_job_writes]]，含承重的 effective_type/compute_next_run
顺序 zombie-bug 修复），backend agent-scoped 路由 POST `/jobs/{id}/update` 与前端
`/api/jobs/{id}` PUT 调同一函数 → byte-parity 且不再三份手抄。失败键 `message`(+job_id)；
HttpStore 传输降级经 `_write_message_key` 把 error→message、`_seg` 编码 job_id 路径段。
cross-agent 读作 not found（安全，改了旧工具漏存在性的措辞）。

## 2026-08-10 (PR-8) — job 读 by_id/semantic/by_keywords 迁入 seam

三个读方法委托给 [[job_module]] 包导出的 `fetch_job_by_id`/`search_jobs_semantic`/
`search_jobs_by_keywords`（[[_job_reads]]，走 JobRepository 方言安全、自兜底不抛），
backend [[jobs]] 孪生路由调同一批 → byte-parity。DirectStore 外层 try 只兜 `_db()`。
HttpStore GET `/jobs/{id}`、POST `/jobs/search-semantic|search-keywords`；失败键 `error`
与 _parse_dict 降级键一致、无需 remap。HttpStore 的 URL 路径段（narrative_id/
event_id/job_id 等 LLM 供给）用 `_seg`(quote safe="") 编码，防 `/`/`..` 改写请求目标
（否则 Direct 报 not found、Http 报 404，parity 缝）。limit 两 store 都 `_clamp_limit`(≤100) 对齐路由
`Field(le=100)`。invalid-status 文案由共享 helper 产出（两路一致）。**输入契约**：`_job_query_reject`(query 1-512)/`_job_keywords_reject`(keywords≥1) 镜像路由 Field，两 store 发车前都跑（否则空/超长 query、空 keywords 本地 success、云端 422 分叉——同 memory/social 的 `_*_reject`）。

## 2026-08-10 (PR-7) — basic_info view_narrative/view_event/switch_narrative 迁入 seam

三个读方法委托给 [[basic_info_module/_narrative_reads]] 的 `fetch_narrative_view`/
`fetch_event_view`/`check_narrative_switch`——这些是**方言安全**（get_one/get/
get_by_ids，无裸 SQL）且自兜底（返回 dict 不抛）的共享函数，backend [[narrative]]
路由调**同一批**函数，故 Direct/Http 逐字相同。DirectStore 外层只包 `_db()` 获取的
try（fetch_* 自身不抛）保住 invariant。HttpStore GET `/narratives/{id}`、
`/events/{id}`、POST `/narratives/{id}/switch`；失败键是 `error`（与 _parse_dict
降级键一致，无需 remap）。**安全副作用**：旧裸 SQL 不校验 agent_id（跨租户读），
迁移后按调用方 agent 归属过滤。

## 2026-08-10 (PR-6) — social create_agent 迁入 seam（社交模块全部迁完）

最后一个 social 工具。**id 归属是 parity 关键**：`new_agent_id` 由**工具**用
`uuid4` 铸造后作为**入参**传进 seam（不再工具/路由各自随机生成），DirectStore 与
create-agent 路由用同一 id provision → 输出逐字相同（否则随机 id 无法 parity）。
DirectStore 解析 creator owner（AgentRepository）→ `provision_new_agent` → 共享
[[social_network_module]] 的 `format_create_agent_success`（含 warnings 上浮，
incident #5，工具旧版本丢了 warnings，现统一上浮）。无 owner 用共享
`CREATE_AGENT_NO_OWNER_MSG`。失败键：DirectStore `message` / 路由 `error`（含
异常统一 `f"Error: {e}"`）→ HttpStore `_write_message_key` 逆映射。DirectStore
不抛（invariant）。路由 body 加 `new_agent_id`，**用 `pattern=^agent_[0-9a-f]{12}$`
约束**——该 id 会成为 workspace 路径段（base/{user_id}/{agent_id}），无约束的
`../victim/agent` 会跨租户写入；[[provision]] 的 `provision_new_agent` 再做一次
`_SAFE_AGENT_ID` 兜底（唯一 seam 覆盖所有调用方，铁律 #5）。

## 2026-08-10 (PR-5) — social 读 search/contact/stats 迁入 seam

3 个读工具走 seam。DirectStore 复用 `_social_module`（try/except 不抛），调
`search_network`/`recall_entity_info`/`get_agent_stats`。**结果整形共享**：
contact 用 [[social_network_module]] 的 `format_contact_result`、stats 用
`format_stats_result`（DirectStore + 新路由同源，杜绝漂移）；search 原样透传。
HttpStore 调**新建的 POST 孪生路由** `/social-network/{recall,contact,stats}`
（POST 避开 GET `/{user_id}` 路径参数冲突；路由直接返回工具 shape=message 键、
不 normalize，故 HttpStore 2xx 原样透传，`_write_message_key` 只兜自身传输降级）。
**输入契约**：`_social_search_reject`（search_keyword 1-512）、`_clamp_limit`(top_k≤100)、
`_social_id_reject`(contact entity_id) 两 store 都跑。no-instance：search/stats 带
`results:[]`、contact 不带（各按工具 shape）。方法全 repository 无裸 SQL、双方言安全。

## 2026-08-10 (PR-4) — social 写 extract/merge/delete 迁入 seam

第三个模块（3 个写工具）。DirectStore 复刻工具体：`_social_module` 解析
SocialNetworkModule 实例（懒 import 避循环，同工具 `_get_instance_and_module`）
+ 构 temp_module + 调 `extract_and_update_entity_info`/`merge_entities`/
`delete_entity`，失败保工具的 **`message`** 键。HttpStore 调 PR-2 已建的
byte-parity 写路由（`/social-network/{extract,merge,delete-entity}`）。
**唯一 parity 坑=失败键**：路由用 `_normalize_write_result` 把 `message`→`error`
（HTTP 家族约定）；`_write_message_key` 是其**精确逆**（`error`→`message`），
把路由响应 + HttpStore 自身传输降级统一回工具的 `message` 形状。成立前提=三个
写方法**只用 `message` 失败**（实测）。实例缺失文案走 [[social_network_module]]
新增的共享 `social_instance_not_found_msg`（route+DirectStore 同源，杜绝漂移）。
方法全走 repository（SocialNetworkRepository），**无裸 SQL、双方言安全**。
**DirectStore 不抛异常**（管线预审二轮 Important）：`_social_module` 把实例解析
包 try/except、三方法调用处也各有 try/except，本地 db/解析故障返回
`{success:False, message:"Error: ..."}` 而非抛错——否则同一故障 HttpStore 是
message dict、Direct 抛错，parity 破。extract 工具里的 `setup_mcp_llm_context`
**已删**（预审二轮 Important）：它读 `agents` 表 + 会 raise LLMConfigNotConfigured，
而 extract 方法纯 repository 不用 LLM——留着既给 mcp 加 db 依赖又是异常破口。
**输入契约**：`_social_id_reject` 镜像路由的 entity-id `Field(1..128)` 边界，两个
store 发请求/查库前都跑（同 memory 的 `_*_reject` 模式），否则空 id 本地会建空实体
返回 success、云端 422 分叉；**严格按 Field 长度语义、不 strip**（路由 min_length
计字符、接受空白 id，strip 会自造分叉）。社交**读**（search/contact/stats）需另建
孪生路由，另个 PR。

## 2026-08-10 (PR-3) — general_memory 的 remember / memory_retain 迁入 seam

第二个走 seam 的模块(继 awareness)。两个工具改为
`get_agent_data_store().remember/memory_retain`;DirectStore 复刻原工具体
(MemoryCoordinator/MemoryEngine,**全走 repository 层无裸 SQL——双方言安全**,
这也是它先于 chat 迁移的原因:chat 的 get_chat_history 是 information_schema
+反引号的 MySQL-only 裸 SQL,迁移要先脱裸 SQL),HttpStore 调
`/api/agents/{id}/memory/remember|retain`。backend 路由返回与工具**逐字同
shape** 的 dict,Http 2xx 直接透传 body;非 2xx/不可达降级为工具自身失败 dict
(never 异常)。渲染统一走 [[coordinator]] 的 `format_memory_hits`(唯一真源,
三处 import 同一函数,不再抄拷贝)。
**输入契约共享(真 parity)**:`_clamp_limit`/`_remember_reject`/`_retain_reject`
镜像路由的 pydantic Field 边界(query 1-512、limit 1-100、content/source ≤上限),
**两个 store 都先跑一遍**,所以本地不会在云端会 422 的入参上成功;limit>100 这类
常见越界被 clamp 到 100 而非硬失败。`_send` 收敛为唯一传输层(一个 AsyncClient +
一个 HTTPError 边界,awareness/get/post 共用);`_parse_dict` 把 422 单拎出来给
agent 一条可据以改参的 in-band 消息,不与 401/502 混。
**grep_memory 暂不迁**:HTTP 侧因 ReDoS 拒 regex(PR-2),严格 parity 需先落
timeout-safe regex 引擎(已记 todo),故 grep 仍走 DirectStore 直连——这也意味着
general_memory 的 mcp 容器现仍需 db 凭据,RCE 收益要到 grep 迁完才兑现。


## Why it exists

The data-access seam MCP tools depend on instead of touching repositories/db
directly (blueprint P0). Lets the transport be swapped by the composition root
without changing any tool (rule #9/#20).

## Model

`AgentDataStore` protocol; two impls:
- `DirectStore` — direct repository access, byte-for-byte the pre-abstraction
  behaviour (local `bash run.sh` / DMG own the sqlite db). Db access goes
  through `XYZBaseModule.get_mcp_db_client` — the one loop-aware MCP entry
  point every other tool uses.
- `HttpStore` — calls the backend API forwarding the caller identity headers;
  mcp holds NO db creds (the RCE-remediation goal). Rule #21: HTTP hop, not
  import. Identity gets the call PAST auth (Q6); per-route OWNER checks are
  PR-2's work — do not claim them before they exist.

The interface grows one method per migrated tool (awareness's update is
first; a read method arrives with its first real caller — YAGNI). Both impls
MUST return the SAME strings for the same scenario, and the parity tests
compare the two implementations against one shared fake-backend semantics,
not each against a constant.

## The backend response contract (pre-review C1/C2 — the load-bearing part)

- The agents routes report failure as **HTTP 200 + `{"success": false,
  "error": ...}`** — non-2xx only ever comes from transport/middleware (e.g.
  the Q6 identity 401). An Http method must parse the body; a status-code
  check calls every failure a success.
- The PUT route's convenience default AUTO-CREATES a missing instance (the
  frontend contract). HttpStore opts out via `create_missing=false` so an
  unknown, LLM-supplied agent_id stays an ERROR exactly like DirectStore —
  without that switch the Http path would mint instances for arbitrary ids.
- HTTP-layer failures (401/5xx/unreachable/non-JSON) degrade to in-band
  `"Error: ..."` strings, never exceptions — DirectStore only ever returns
  strings, and a 401 here means the deploy flipped NARRANEXUS_BACKEND_URL
  before provisioning identity keys (ordering contract in factory.py).

## Gotchas

- DirectStore does NOT check the upsert's boolean result — faithful to the
  pre-seam tool. The Http path surfaces the backend's "Failed to update
  awareness". A known, documented asymmetry: Http is strictly more honest,
  Direct is bug-compatible; fixing Direct means changing local behaviour and
  belongs to its own change, not this seam.

## 2026-09-04 · data-access providers (batch 3c.4)

`DirectStore` no longer imports any builtin module: each capability method resolves its body by name from `agent.capabilities.data_access` (`_handler`, on the injected registries or the process kernel registries) and passes the db client first. What stays here is platform policy — the parity rejects/clamps, the never-raise wrapping per failure shape (`_dict_call`, `_social`, `_job_write`), `_awareness_instance_id` (a platform InstanceRepository query the awareness provider receives the result of) and the message constants — plus `_unavailable_msg` for a provider that is absent because its builtin is disabled. `HttpStore` is untouched.

## 2026-09-04 · awareness by role (batch 5d)

The awareness instance lookup asks `module_by_role("awareness")`; None when that plugin is disabled.
