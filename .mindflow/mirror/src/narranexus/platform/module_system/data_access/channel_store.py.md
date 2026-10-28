---
code_file: src/narranexus/platform/module_system/data_access/channel_store.py
stub: false
last_verified: 2026-09-07
---

## 2026-09-07 — `_ChannelSpecs._build` 变成纯读，并删掉死 import

两处改动，同一个主题：

- 删掉 `_registry()` 里的 `import narranexus.platform.module_system  # noqa: F401 —
  registers the builtin descriptors`。`register_all` 已不存在，这个 import 什么也不注册。
- 删掉 `_build()` 里的 `WorkingSource.register(d.name)`。`SUPPORTED_CHANNELS` 的成员判断
  在 HTTP 请求路径上（`backend/routes/agents/channel_credentials.py`），而这一行让「读一
  下渠道表」变成「改两个进程级开放枚举」。注册收归 contribution 时刻。

没 boot 的进程里的行为不变、并且被测试钉住：`CHANNELS` 为空、`SUPPORTED_CHANNELS` 不含
任何名字、按名取用抛 `KeyError` / `UnknownChannel`，而不是悄悄放行。
## 2026-08-11 (三轮审查) — 写失败信封用稳定错误码，不泄漏 DB 连接文本

`_run_mutation` 的失败信封原来放 `str(e)`——连接期异常（aiomysql `Can't connect to … (111)`）带**主机/端口/用户名**，会经信封→工具→model→用户。改为：`_db()` 失败→`"db_unavailable"`、写失败（非 ValueError）→`"write_failed"`，细节只进 `logger.exception`。`ValueError` 单独接住（apply_patch 的「no credential」/「unknown field」是安全可行动信息，原样透出）。守卫测试 [[test_channel_store]]（含断言 host/user 不出现在信封）。

## 2026-08-11 (二轮审查) — 模块头「已知缺口」改写为「已迁完」+ _run_mutation 守 _db()

模块 docstring 的 "Known gap"（说 lark CLI-OAuth 写 + narra 透传**未迁**、无后端路由、直连 db、strip DB_PASSWORD「不算数」）已**过时且矛盾**——本分支正是迁了这些。是 gates strip 决策的门面文档，误导 ops。改写为「已迁完：CLI 子进程留本地、DB 持久化经 patch/put/delete 原语；三文件 get_mcp_db_client==0；整个 mcp 模块树零凭据、可 strip DATABASE_URL」。另：`_run_mutation` 把 `await self._db()` 包进 try→失败返信封（写路径流入无 try 的 lark 工具、现在检查信封，裸抛会甩给 model）。

## 2026-08-11 (审查收口) — DirectStore 写原语永不抛（对齐 seam 契约）+ 能力守卫

`patch/put/delete_credential` 合成一个 `_run_mutation`：**运行时失败 catch→`{success:False,error}`**（不再让 apply_patch 的 ValueError 冒泡），这样 DirectStore 与 HttpStore **失败语义对齐**——原来本地抛、云端返信封，导致工具写助手在云端把失败静默当成功（预审 Important）。能力缺失（非 lark channel 无 apply_patch/save_raw）→**清晰 ValueError**（像 test_connection），不再裸 AttributeError→500。守卫测试 [[test_channel_store]]：runtime 失败降级成信封 + 不支持 channel 报 ValueError。

## 2026-08-11 (lark 收尾) — lark 入 descriptor：bind + unbind_service

lark 的 `ChannelSpec` 加 `bind=_BindSpec(_lark_service, mgr, has_test=False)`(绑已有 app)+ 新字段 `unbind_service`(do_unbind——unbind 不止删凭据、还拆 inbox 频道，非 mgr.unbind)。DirectStore.unbind 按 unbind_service 分支调 `do_unbind(mgr,agent_id,db)` 返其信封；HttpStore.unbind 打 /api/lark/unbind。**byte-parity 前提**：该路由必须**原样返回** do_unbind 的 dict（2026-08-11 修——原路由 reshape 成旧 `{success:True}`/hoist error，与 Direct 不对齐；见 [[lark]] 路由 mirror）。CLI-OAuth 写走 patch/put/delete 原语。

## 2026-08-11 (lark 原语) — credential-mutation primitives + deep_merge

Protocol/DirectStore/HttpStore 加 `patch_credential`/`put_credential`/`delete_credential`——**通用凭据变更三原语**(任何 manager 实现 apply_patch/save_raw/delete_credential 即可经它写，不用每操作一路由)。模块级 `deep_merge`(嵌套 dict 递归合、标量替换)是 PATCH 承诺的合并语义、manager.apply_patch 用它，本地云端合并一致。HttpStore `_post_json`→`_send_json(method,…)` 泛化支持 PATCH/PUT/DELETE，写侧 never-raise→{success:False} 契约不变。lark 是首个用户。

## 2026-08-11 (lark 地基) — 三张平行表收成单 ChannelSpec descriptor

`_MANAGER_REGISTRY`+`_BIND_SERVICE`+`_DISPLAY_NAME` 收成 **一个 `CHANNELS: dict[str, ChannelSpec]`**（review 一直诟病的三表漂移根除）。`ChannelSpec`=manager_module/class + read_method + display_name(unbind 措辞) + 可选 `_BindSpec`(do_bind 服务 + takes=mgr|db + has_test)。`_spec/_manager_class/_read_method_name` 及 DirectStore.bind/unbind/test_connection 全读单字段；`SUPPORTED_CHANNELS=frozenset(CHANNELS)`。**加 channel 真的=一行 ChannelSpec + to_raw_dict**。纯重构、行为不变（55 测试绿）。为 lark 写原语(patch/put/delete)打地基。

## 2026-08-11 (审查 round-3 收口)

模块头 3 处矛盾扫清：`_MANAGER_FOR`→`_MANAGER_REGISTRY`(全仓不存在的符号)；HttpStore 段改成如实的「读 GET→None / 写 POST→{success:False}」两种降级；并如实说明**加 channel 不是一行**——读走 _MANAGER_REGISTRY、写走 _BIND_SERVICE、unbind 文案走 _DISPLAY_NAME(三张平行表，未来可收成单 descriptor)。`_DISPLAY_NAME` 精确到**有 seam-unbind 工具的 4 个 channel**(discord/slack/telegram/wechat)并注明 narra/lark/HA 为何不入表(消除�the漂移质疑)。端点测试：假中间件用 `x-test-unverify` 把 `nx_service_authed` 与前缀**解耦**，新增「前缀在但未验签→403」一条(对旧前缀实现会红)，真正锁住「门读已验签 flag」。补 get_agent_owner Direct↔Http parity。删 slack/telegram 死 import + 测试死代码。

## 2026-08-11 (审查 round-2 收口)

管线审查清零：模块/Protocol/HttpStore docstring 全扫新（Protocol 不再写 read-only、HttpStore 写「GET→None / POST→{success:False}」两种降级、删对 gitignored spec 的死指针，铁律 #22）。`_DISPLAY_NAME` 让 DirectStore.unbind 的 not-found 措辞与路由 byte 一致(no **Discord**…)。test_connection 用 `_BIND_SERVICE.get` 避免不在表内的 channel(wechat/lark/HA)抛 KeyError→统一清晰 ValueError。HttpStore.bind 注明 route Pydantic 值约束会 422、降级成 {success:False}(never-raise 不破，仅坏输入措辞不同)。

## 2026-08-11 (PR-H 预审收口) — 删 get_manager + test_connection 守卫

写侧预审 Minor 清理：`DirectStore.get_manager()`（0 调用方，lark 也没用）**已删**（铁律 #2/#8）——lark 写另立时再加。`test_connection` 对无 `do_test_connection` 的 channel（narra）给**清晰 ValueError** 而非裸 AttributeError；`unbind` docstring 记下 **narra 路由 unbind 是扁平 `{success,unbound}` 且空删也报成功**、与本 seam 嵌套 envelope 不符——未来若加 narra_unbind 工具须先对齐(narra 现只有 bind 工具，分歧 inert)。补测：narra db-taker bind 分支/test_connection(两 store)+守卫/non-JSON 写降级。

## 2026-08-11 (PR-H) — 写侧 seam：bind/unbind/test_connection

Protocol 加 `bind/unbind/test_connection`。unbind/test_connection **契约统一**(mgr.unbind / do_test_connection)；bind **异构**由 `_BIND_SERVICE`(模块,函数,takes=mgr|db)分派——discord/slack/telegram 收 mgr、narra 收 db。DirectStore 复刻路由 envelope(`{success,data:{unbound}}`)；HttpStore `_post_json` POST `/api/<ch>/<op>`、**never-raise 但返 `{success:False,error}`**(失败的 unbind 不是'已解绑')。lark 写整体排除(CLI-OAuth，另立)。已迁写：discord/slack/telegram/wechat(unbind)/narramessenger(bind)。


## 2026-08-10 (PR-A) — ChannelCredentialStore：per-channel 送信凭据的 MCP 取数 seam

[[store]] 的 AgentDataStore 搬的是叙事数据；这是**另一条** seam（Owner 拍板：独立 Protocol，不并入），
因为它返回的是 send 工具认证外部平台要用的**原始密钥**（Discord `bot_token`、Lark `app_secret`…），
性质与叙事数据不同、安全敏感，故自成 Protocol，只复用同一套 env-gated DirectStore/HttpStore 传输形态。

- **DirectStore**：`_manager_class(channel)` 懒解析到各 channel 的凭据管理器，调其 `get(agent_id)` 后
  用 `to_raw_dict()`（**不是** `to_public_dict()`——本 seam 存在的全部理由就是把密钥带过 HTTP 跳）序列化。
- **HttpStore**：GET owner-gated 后端端点 [[channel_credentials]]，转发身份头（同 [[factory]] current_identity_headers）。
  **绝不抛**：unreachable / 非 2xx / 未绑定 一律降级成 `None`，让 send 工具落到它既有的 "no_credential" 分支，
  不炸掉 agent 的一轮。`_seg` 按路径段百分号编码 LLM 可控 id（同 store.py 理由）。

channel→manager 用 `_MANAGER_REGISTRY`（channel → (模块路径, 类名, **读方法名**)，按名懒 import）集中登记，`SUPPORTED_CHANNELS`
由它派生（端点 allowlist 不会与 DirectStore 能解析的漂移）。读方法名**不统一**（discord/slack/telegram/wechat=`get`，lark=`get_credential`）
故入表第三字段，`_read_method_name` 取之、DirectStore 用 getattr 调用。加一个 channel = 注册表加一行 + 该 channel 凭据类加 `to_raw_dict`/`_cred_from_raw`，
seam / 端点 / allowlist 全跟随。已接入：discord/slack/telegram/wechat/narramessenger/lark/home_assistant（读侧；lark 读方法名 `get_credential`；HA 用薄 repository 适配器，raw=config_json）。
Protocol 除 `get_credential`/`get_agent_name` 外加 `get_agent_owner`（agents.created_by）——narra 的媒体发送 + CLI 工作区解析要它；DirectStore 读库、HttpStore 打 `/channels/owner`。**已知缺口（明写不藏）**：写/生命周期（bind/unbind/setup）尚未进本 Protocol，
`DirectStore.get_manager()` 是给 discord 写工具的**本地专用**便利、无 HttpStore 对应——所以云端写工具仍需本地 db，
`DB_PASSWORD` 要等写路径也迁移（另一 PR）才能从 mcp 摘掉。这是 #2「mcp 零 db 凭据」的收口条件。

## 2026-09-04 · channels as descriptors (batch 4a)

`CHANNELS` is `_ChannelSpecs`, a live Mapping over `ingress.channels` (ChannelSpec derived from each descriptor's manager / read method / bind-test service / unbind service); `SUPPORTED_CHANNELS` is the live name set. The hand-written table is gone: adding a channel is a descriptor in its plugin, and a disabled builtin answers "unknown channel".

## 2026-09-04 · webhook transport (batch 4c)

`_ChannelSpecs._build` registers the `WorkingSource` of every inbound descriptor, so a plugin channel's turns have their source in any process that reads channels.

## 2026-09-04 · HttpStore on the generic routes; unbind follows `bind_takes` (batch 4d.3)

`HttpStore.bind/unbind/test_connection` POST `/api/channels/<channel>/{bind,unbind,test}` (bind body `{agent_id, fields}`) — the per-channel routes are retired. `ChannelSpec.unbind_takes` mirrors the descriptor's `bind_takes`, so a channel whose service unbind is `do_unbind(db, agent_id)` (narramessenger, now `unbind_service=True`) is called with the right signature instead of the lark-shaped `(mgr, agent_id, db)`.

## 2026-09-04 · no narramessenger special case (batch 4e)

`display_name` is the descriptor's for every channel; NarraMessenger's unbind goes through its service (`unbind_service`), so the old "" special case was dead.

## 2026-09-07 — _ChannelSpecs: per-entry isolation and a cache keyed on registry state

One descriptor factory that raised made CHANNELS/SUPPORTED_CHANNELS raise and 500'd the generic credential endpoint for every channel; now a broken descriptor is logged and absent (fail closed for that channel only, mirroring TriggerMapView). The built dict is cached on the registry's name tuple (ModuleRegistry's pattern) so a per-request membership test no longer re-resolves every descriptor N+1 times; WorkingSource.register therefore runs once per registry state, not once per read.
