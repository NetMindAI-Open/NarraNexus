---
code_dir: src/xyz_agent_context/channel/
last_verified: 2026-04-10
stub: false
---

# channel/ — IM 渠道通用抽象层

## 目录角色

`channel/` 是所有 IM 渠道 Module（Matrix、未来的 Slack/Discord/Email）的**共享基础设施**，不属于任何单个 Module。它定义了渠道消息处理的标准模式，让每个渠道 Module 只需要实现"如何取数据"，而不需要重复实现"如何组装 prompt"。

四个文件分别负责：
- `ChannelContextBuilderBase`：Template Method 模式的 prompt 组装基类
- `ChannelSenderRegistry`：进程级单例注册表，记录哪些渠道可以发送消息
- `channel_prompts.py`：所有渠道共用的 prompt 模板（消息主模板、发件人档案、历史记录、群成员）
- `channel_contact_utils.py`：`contact_info.channels` 字段的读写工具，统一处理格式规范化

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `channel_context_builder_base.py` | 定义渠道 prompt 的组装流程（Template Method），子类实现数据获取 |
| `channel_sender_registry.py` | 进程级 class-level 注册表，渠道 Module 在 init 时注册发送函数 |
| `channel_prompts.py` | 主执行模板 + 辅助模板（发件人档案、历史记录、群成员列表）|
| `channel_contact_utils.py` | contact_info JSON 字段的 deep merge、格式规范化、channel-specific 读写 |

## 和外部目录的协作

**上游（生产者）**：具体渠道 Module（目前只有 `module/matrix_module/`）继承 `ChannelContextBuilderBase`，在 `get_mcp_config()` 或 Module init 时调用 `ChannelSenderRegistry.register()`。

**下游（消费者）**：
- `agent_runtime/` 的触发路径（MatrixTrigger 等）调用 `build_prompt()` 生成输入内容
- `narrative/_narrative_impl/continuity.py` 的 `_extract_core_content()` 依赖 `channel_prompts.py` 的 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 格式（以 `[Matrix · ...]` 开头的标记），做内容剥离

**注意**：`channel/` 和 `schema/channel_tag.py` 名称相似但职责不同——`channel_tag.py` 是触发源标识符，`channel/` 是 prompt 构建工具。两者都被 Matrix 渠道使用，但互不依赖。

## 如何注册新渠道

1. 在 `module/` 下创建新 Module 目录（如 `slack_module/`）
2. Module 的 `get_mcp_config()` 或初始化时调用 `ChannelSenderRegistry.register("slack", slack_send_fn)`
3. 继承 `ChannelContextBuilderBase` 并实现三个抽象方法：`get_message_info()`、`get_conversation_history()`、`get_room_members()`
4. 在 `channel/channel_contact_utils.py` 的 `known_channels` 集合里添加新渠道名，确保 `normalize_contact_info()` 能识别它
5. 如果渠道有特殊的消息格式头（类似 Matrix 的 `[Matrix · ...]`），需要在 `narrative/_narrative_impl/continuity.py` 的 `_extract_core_content()` 里添加对应剥离逻辑
