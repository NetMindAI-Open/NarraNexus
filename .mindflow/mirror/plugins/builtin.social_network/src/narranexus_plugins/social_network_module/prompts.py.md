---
code_file: plugins/builtin.social_network/src/narranexus_plugins/social_network_module/prompts.py
last_verified: 2026-09-04
---

## 2026-08-21 — 新增「跨渠道触达」流程指令(PR-2 WS-D)

`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 的第 3 节后加子节 **§3b「Reaching someone on a channel you are NOT currently in」**。教 agent:你不被困在触发会话;要触达别处的人/agent(你已连接且此前触达过的渠道)——① `search_social_network` 结果已含 `contact_info`;② 读 `contact_info.channels`,每个 key 是一个渠道,`rooms[你的agent_id]` = 该用哪个会话 id,`preferred_channel` 是对方偏好;③ 调那个渠道自己的 send 工具(`*_send(room_id, text)`,绑定后每轮在桌上)。没有对应 channels 条目 = 没有已记录的触达方式,**明说,别猜 id**。

配套事实:reach 由 [[inbox_recorder.py]] 自动记录(prompt 里点明「automatically」),所以 `contact_info.channels` 随交互自动填充;send 工具跨 surface 可用(绑定即在桌,`ChannelModuleBase.disallowed_tools` 只按未绑定压制、不按触发渠道——`test_setup_residency.py` 新增 cross-surface 锁)。守卫 `tests/social_network_module/test_reach_flow.py`(措辞锁 + `format_contact_result` 返回 channels)。这是「能力跟着 agent 走」从内部 bus(PR-1)扩到外部 IM 的用户可见落点。

**增量审 Important**:「自动填充」是**过度承诺**——只记 1:1,群里遇到的人不记(Slack `C`/`G`、Discord 非 DM 大部分流量落在不记分支)。§3b 收敛为「**automatically for your 1:1 conversations**;只在群里遇到的人不这样记——群房间不是找单人的方式——所以 `search` 可能查不到他的渠道,这是预期」。措辞锁 `test_reach_flow.py`(断 `automatically for your` + `1:1 conversations` + `only met in a GROUP is not recorded`)。`channel_prompts.py` #5 同步收成「1:1 才自动捕获」。

**预审收敛**:①step3 原举例 `*_send(room_id, text)` 与真实工具不符(lark/slack/tg 是 `*_cli`、微信要 per-inbound `context_token`)→ 改成「渠道各有各的调用形态,有的是 CLI(如 `lark_cli`),查渠道自己的工具」;加第二条限制「有些渠道只能在本轮活跃会话内投递,做不到就明说别猜」(surface-blind,不点名 WeChat,binding rule #4)。②不再逐个点名渠道(`on any IM channel you are bound to`)——避免社交模块持有第三份渠道名册。

## 2026-07-28 — R4b：新增 SOCIAL_NETWORK_MODULE_INSTRUCTIONS_STABLE

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

legacy 模板一字未动；stable 版由 `.replace()` 把 §5 的
`{social_network_current_entity}` 占位符换成静态指引句（实体卡在本轮消息
turn context 块的 "Current User Information" 小节里）推导得出。§5 标题保留
（§1-6 编号不断档），§1-4/§6 规则字节不变。动机：实体卡的
interaction_count / last_interaction_time 每轮递增，打穿 system prompt 前缀
缓存。replace 锚点 = `"##### 5. Current User Information\n{social_network_current_entity}\n"`
——改 §5 措辞时锚点必须同步，`tests/module/test_turn_context_split.py` 兜底。

## 2026-07-23 — extraction prompt: Confidence section

`BATCH_ENTITY_EXTRACTION_INSTRUCTIONS` now asks for a 0-1 `confidence`
per entity (low = might be a concept/role/artifact → discarded). Pairs
with the deterministic guard in [[_entity_updater.py]].

# prompts.py — SocialNetworkModule 提示词集合

## 为什么存在

集中管理 SocialNetworkModule 所有用到的提示词字符串，避免散落在 `social_network_module.py` 和 `_entity_updater.py` 里。分两类：一是 `SOCIAL_NETWORK_MODULE_INSTRUCTIONS`，注入到系统提示里告诉 Agent 如何使用社交网络功能；二是四个 LLM 操作提示词（`ENTITY_SUMMARY_INSTRUCTIONS`、`DESCRIPTION_COMPRESSION_INSTRUCTIONS`、`PERSONA_INFERENCE_INSTRUCTIONS`、`BATCH_ENTITY_EXTRACTION_INSTRUCTIONS`），由 `_entity_updater.py` 的各个函数调用。

## 上下游关系

- **被谁用**：`SocialNetworkModule.__init__()` 引用 `SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 初始化 `self.instructions`；`_entity_updater.py` 里的各个 LLM 调用函数各自引用对应的操作提示词
- **依赖谁**：无（纯字符串常量）

## 设计决策

**`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 的核心约束**：指令里有大量关于"何时记录"（立即，不要等待或请求许可）、"标签纪律"（少而精，用规范形式）、"实体 ID 规则"（当前用户用 `user_id`，其他人用 `entity_{name}_{timestamp}`）的规范性说明。这些约束直接决定了 Agent 的社交记忆质量——如果 Agent 不遵守标签纪律，几周后每个实体就会积累几十个无意义的标签。

**`{agent_id}` 占位符**：`SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 里有 `{agent_id}` 占位符，在 `SocialNetworkModule.__init__()` 里用 `.replace("{agent_id}", agent_id)` 替换，而不是通过 Python f-string 或 `.format()`。这是因为指令里可能有其他花括号（如示例代码或 JSON 格式），用 `.replace()` 只替换指定变量，不会意外处理其他花括号。

**`{social_network_current_entity}` 没有在这里定义**：这个占位符出现在 `SOCIAL_NETWORK_MODULE_INSTRUCTIONS` 里，但它是运行时由 `gather` 通过 `ctx_data.social_network_current_entity` 注入、再由 `contribute_instructions()` 格式化填充的。阅读代码时注意这个两步替换机制。

## Gotcha / 边界情况

- **`BATCH_ENTITY_EXTRACTION_INSTRUCTIONS` 要求 LLM 不提取主发言人本身**：批量提取提示词里明确说"不包含正在交互的主要用户（primary speaker）"。如果 Agent 提取了主用户自己（`entity_id` 和 `user_id` 相同），会在 `after_turn` 里被 `extract_mentioned_entities()` 过滤掉（通过 `primary_entity_name` 参数排除）。

## 新人易踩的坑

- 修改 `PERSONA_INFERENCE_INSTRUCTIONS` 时要确保要求 LLM 返回的字段与 `_entity_updater.PersonaOutput` Pydantic 模型一致（目前只有一个字段 `persona: str`）。如果在提示词里要求更多返回字段但忘记更新 Pydantic 模型，LLM 会返回额外内容但会被丢弃。
