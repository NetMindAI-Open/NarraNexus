---
code_file: src/xyz_agent_context/module/_mcp_identity.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 — user_id 上线同一 seam（W1），纪律刻意弱于 agent_id

`X-NarraNexus-User-Id` + bearer 第 5 位（`BEARER_FIELDS` 尾部追加，老
4 字段 bearer 解析出 user_id=None，兼容由既有 `_parse_bearer` 无界
split+截断保证）。动机是 job_create 的最恶性失败形状：模型猜错
user_id 时 job **建成功**（success=True）但落在幻影用户名下——属主的
Jobs 列表永远空着，agent 却报成功（假成功）。

**与 agent_id 的三点刻意不同**（`resolve_caller_user_id` docstring 是
契约原文）：
1. **占位符才注入**（`user_current` 等 `PLACEHOLDER_USER_IDS`）；
2. **None 永远不碰**——检索工具用 `user_id=None` 表达「不过滤」，注入
   会静默改变查询语义；`is_placeholder_user_id(None)` 为 False（与
   `is_placeholder_agent_id` 极性相反，两个函数不能互换）；也因此
   wrapper 里 user_id **没有** agent_id 那个「缺省参数则注入」分支。
3. **mismatch 保留原值只 warning**——平台对「它启动了谁」只有一个真相，
   但多用户流程里传别人 user_id 可能合法（销售/团队场景），先用
   warning 计量再决定是否收紧（PR #230 的先测量纪律）。

`resolve_caller_user_id(None)` 在函数入口直接返回，不读取请求 header；None
分支本来就禁止注入，读取结果必定被丢弃。`install_caller_identity` 与
`_wrap_fn` 的 docstring 同步写明 wrapper 覆盖 agent_id **或** user_id，避免
维护者按过期的 agent_id-only 描述误判影响范围。

`install_caller_identity` 的 wrap 判据从「声明 agent_id」放宽为
「声明 agent_id 或 user_id」。注入端 `agent_id_headers` 增加
`user_id=None` 参数（None 时 header 省略、bearer 尾字段掉落），
[[context_runtime]] 盖章处传 `self.user_id`。测试：
`tests/module/test_mcp_caller_user_identity.py` +
`test_mcp_identity_injection.py::test_mcp_spec_carries_the_turn_owner`。

## 2026-08-03 — bearer 变成「位置记录 + 钉死字段数」,解析器合成一个

`BEARER_FIELDS = (agent_id, turn_source, errand_peer, errand_channel)` 是字段
数与顺序的唯一真相,约定写在 `BEARER_FIELD_SEP` 旁边:位置固定、尾部空字段不
上线(读者必须容忍 1~N 个字段)、中间空字段合法表示"未知"、新事实只能**追加**
在末尾。

**为什么必须收成一个 `_parse_bearer`**:原先两个读者各自
`split(SEP, 1)`——真加第三段时,`caller_turn_source()` 会把
`<source>~<第三段>` 整段当 turn source 返回,而这正是本轮要加第三、四段的
前置雷(review round 3/4 点名)。现在:
- `_parse_bearer` 用**无界 split 再截断**。有界 split 会把多出来的段粘在
  最后一个命名字段上(`ch_1~future_fact` 被当成 channel id)——更新的发送方
  应该让老读者"读不到",而不是"读错"。
- `_ambient_headers` / `_explicit_header` / `_bearer` 抽出来共用,三个读者
  (identity / turn source / errand scope)锚定判据只有一份。

新增 `caller_errand_scope()` 与 `X-NarraNexus-Errand-Peer/Channel`:承载
「本轮的差事跟谁、在哪个 channel」。这个事实的消费者是
[[_message_bus_mcp_tools]] 的 `_send_turn_source`——**整轮**只有一个 turn
source,但一轮里既可能追问差事对手、也可能回答另一个 channel 的同伴,
所以判断必须下沉到知道目标的 send 现场(整轮盖章的复发见
[[message_bus_trigger]] 2026-08-03 条)。

## 2026-08-04 — 同一 seam 增加 turn source;注入面公开化(review)

- 新增 `X-NarraNexus-Turn-Source` header + `caller_turn_source()`:工具需要
  知道"这一轮是 owner 面还是同伴面",两个 bus 发送工具据此把事实写到消息上
  (见 [[message_bus_trigger]] 的指令选择)。
- **turn source 同时搭 bearer**:`nx-agent:<agent_id>~<source>`。第一版
  只走显式 header,而 codex 只转发 bearer —— 于是 codex 侧提问方写进去的
  永远是 NULL,接收方每次落到"我说过话吗"那个降级分支,**追问会翻回
  Owner Relay,P1 在该路径未修**(PR #229 review 抓出)。铁律 #15 不许把
  一等适配器当边角情况。分隔符选 `~`:token68 安全(RFC 7235),且不出现在
  我们的 agent id(`agent_` + hex)或任何 source 名里。解析全收在本模块的
  两个函数里,适配器一行未改。真机验证:只发 bearer(模拟 codex 丢掉所有
  自定义 header)时,服务端仍同时取到身份与 source。
- 消费方仍必须把"读不到"当未知(调用方可能自己就不知道 source)。
- **注入面从私有模块提到公开面**:`AGENT_ID_HEADER` /
  `TURN_SOURCE_HEADER` / `agent_id_headers` 经 `module/__init__.py` 导出,
  [[context_runtime]] 改从公开面 import;服务端解析仍留在本私有模块。
  (原来 `_` 私有模块被两个包跨包 import,等于把私有当公共 seam 用。)
- bearer 判据从 `in` 改成**锚定** `startswith(f"Bearer {prefix}")`:真 token
  恰好包含 `nx-agent:` 子串时不会被切一刀。
- 适配层不再 import 本模块常量:codex 适配器改用**命名空间前缀**
  (`x-narranexus-`)判断豁免,避免 agent_framework 反向依赖 module 包。

# _mcp_identity.py — module MCP 工具的调用者身份

## 为什么存在

P1「Agent 消极回复"我做不了"」(2026-08-02 线下 段 06 / evt_0dcee899)。
模块 MCP Server 是**每模块一个进程、所有 agent 共享**,于是 93 个工具的
`agent_id` 参数一直由**模型自己填**。现场模型填了字面量
`agent_id="agent_current"` → `get_by_agent()` 查不到 → 硬错误串 →
agent 告诉用户"技术问题,做不了"(还是英文)。

注意:模块 prompt **本来就告诉过**模型正确 id
([prompts.py](social_network_module/prompts.py) 连说两句
"IMPORTANT: Your agent_id is `agent_x`"、"Always pass agent_id=..."),
替换也真生效。所以这不是"没告诉它",而是**平台把一个机器可知的事实
押在了模型听话上**——按铁律 #15 平台不管用户选什么模型,那就不能因模型
弱而硬失败。

## 工单方案里的一处走不通

工单写「服务端注入 caller 身份(**或**解析 agent_current/self 别名)」。
括号里的不是独立备选:Server 共享,不知道谁在调,`agent_current`
**无法解析**。别名解析必须依赖注入。本模块两件一起做。

## 通道选择:header(2026-08-01 实测,不是推断)

对两个适配器各自的传输实测过(探针见 PR 描述):

| 通道 | SSE(claude) | streamable(codex) |
|---|---|---|
| 自定义 header | ✅ | ✅ |
| `Authorization: Bearer` | ✅ | ✅ |
| URL query `?agent_id=` | ❌ **丢失** | ✅ |

query 在 SSE 上丢失的原因:工具调用 POST 到 `/messages/?session_id=…`,
`/sse` 上的 query string 早没了。**只有 header 两边都通**。

注入两个拼写,读取时任一命中即可——因为两个适配器能表达的东西不同:
- `X-NarraNexus-Agent-Id`:正经写法,claude 适配器原样转发任意 header
- `Authorization: Bearer nx-agent:<id>`:codex 适配器**不能**带任意
  header(见 `adapters/codex/official_sdk.py`:"Codex config cannot carry
  arbitrary HTTP headers"),bearer 是它唯一会发的 header 形状。模块
  MCP 不做鉴权,借用无副作用——但**确实是借用**,所以加 `nx-agent:`
  前缀,永不可能与真 token 混淆(真 bearer 有专门测试挡)。

## 接入点只有一个(不是 14 个)

`XYZBaseModule.build_instrumented_mcp_server()`(base.py 新增)包住子类的
`create_mcp_server()`,ModuleRunner 的两个部署点改调它。于是:

- **零个**模块文件需要改动,**93/93** 个带 `agent_id` 的工具全部覆盖
  (16 个模块,有回归测试逐个断言)
- 新模块声明 `agent_id` 就自动获得,没有"谁忘了加一行"的可能

`install_caller_identity(mcp)` 包的是 `tool.fn`,并显式保留
`__signature__`——FastMCP **已经**用签名建好了 JSON schema,签名和
可调用体必须继续一致(有测试断言 schema 仍声明 `agent_id`)。

## 三层行为

1. 有注入 + 模型填占位符 → 用注入身份(info 日志)
2. 有注入 + 模型填了**别人的 id** → 用注入身份(warning 日志)。
   **顺带的安全收紧**:此前 Server 不校验 `agent_id` 是否属于调用者
   (references/module_system.md §5 原文"运行时是可信的"),现在跨 agent
   读不到别人数据了。所有工具的 `agent_id` 文档都写的是"你自己的 id",
   没有合法用法会传别人的。
3. **无注入**(未来某个不带 header 的适配器 / 直连 MCP 客户端)+ 占位符
   → 返回**可自愈**文案(告诉它去 instructions 里抄真 id),而不是原来
   那种被模型读成"不可能"的死胡同。真 id 则原样通过,行为完全不变。

## 坑

- **返回形状必须跟工具一致**:FastMCP 会按返回注解建 output schema 校验
  结果,给 dict 工具塞 str 只是把一种困惑换成另一种。而注解**可能是
  字符串**——`message_bus_module`(本 bug 最相关的 A2A 模块)就用了
  `from __future__ import annotations`。所以 `_annotation_is_dict` 同时
  认类型和字符串两种形态;**第一版只认类型,恰好会在最要紧的模块上静默
  返回错形状**,是新写的测试当场抓到的。
- **永不成为故障源**:读不到请求(单测直调)、无 header、异常——一律
  回落到"用参数",即原行为。

## 上下游

- 注入方:[[context_runtime]] 的 `mcp_servers` 组装(单点,两个适配器
  共用同一个 spec dict)
- 安装方:[[base]] `build_instrumented_mcp_server` ← [[module_runner]]
- 测试:`tests/module/test_mcp_caller_identity.py`(含全 MODULE_MAP 覆盖断言)

## 2026-08-03 真机验证(两个框架都过了)

- **claude_code 路径**:真实 turn 跑通;并用可区分数据证明了跨 agent 隔离
  (小雀/羽书 都有 `user_tc` 这个实体但名字不同 —— 传对方 id 时拿回的仍是
  自己那条,证明注入身份覆盖了参数)。
- **codex_cli 路径**:真实 turn 跑通(社交网络工具读+写都成功);并单独证明
  身份确实抵达 codex —— 它收到的环境变量
  `NARRANEXUS_MCP_BEARER_…=nx-agent:agent_d8795abf5021`,config 里有对应的
  `bearer_token_env_var`,且 argv 里不含身份值。
- 顺带修掉一个**自己引入的日志噪音**:codex 适配器对"不支持的 header"会
  逐 server 告警,而我给每个模块 server 都注入了
  `X-NarraNexus-Agent-Id` → 每轮 ~16 条。该 header 属于**故意双发**(codex
  带不了才配 bearer),已在 `codex_mcp_bearer_env` 里豁免;豁免刻意做窄,
  用户自己的自定义 header 消失仍然告警(两条测试分别钉住)。
  测试用 loguru sink 而不是 pytest caplog —— loguru 不走 stdlib logging,
  `not in caplog.text` 会因为永远是空串而假通过(第一版就踩了)。
