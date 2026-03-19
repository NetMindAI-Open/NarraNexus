# Weekly Report (2026-03-13 ~ 2026-03-19)

## 用户可感知的新功能

- **同时和多个 Agent 聊天**：不用再等一个 Agent 回完才能跟另一个说话了，可以同时开多个对话，互不干扰
- **聊天记录统一时间线**：历史消息和当前对话合在一起展示，不再有"历史消息在上面"的分割线，往上滑就能加载更早的聊天
- **Agent 后台干活会通知你**：如果你正在看 A 的对话，B 在后台完成了任务，会弹通知提醒你，侧边栏也会显示徽标
- **能看到 Agent 的思考过程**：每条历史消息都可以展开看 Agent 当时在想什么、调用了哪些工具
- **消息可以复制/下载**：每条消息气泡新增了"复制 Markdown"和"下载 .md 文件"按钮
- **Agent 之间可以聊天了（Matrix 通信）**：Agent 可以互相发消息、创建群聊、加入房间，实现多 Agent 协作
- **用聊天装技能**：不用自己翻技能列表了，直接跟系统说"我需要一个能查天气的技能"，系统会自动推荐和安装
- **技能的 API Key 配置**：有些技能需要 API Key 才能用，现在可以在界面上直接填写，不用改配置文件
- **Agent 可以自己创建新 Agent**：通过工具调用，一个 Agent 可以创建另一个 Agent
- **Agent Inbox 加载全部消息**：Matrix 收件箱支持一键加载所有消息，不再受 500 条上限限制
- **看得到花了多少钱**：新增 Token 用量统计面板，能看到每个 Agent 消耗了多少 Token
- **Agent 回复更快了**：优化了内部决策流程，每轮对话节省约 2-3 秒
- **后台活动记录**：Agent 在后台执行任务（定时任务、群聊消息）时，聊天框会以小字显示活动记录，让你知道它做了什么
- **错误信息可见了**：之前 API 报错、限流用户看不到，现在会直接在聊天框里显示红色错误提示
- **Desktop 应用自动更新**：桌面版支持检测新版本并自动更新

## Bug Fixes

- Chat timeline 消息时间乱序：合并 history + session 消息后未排序，现在按 timestamp 排序
- Chat 轮询覆盖已加载历史：用户上滑加载更多后，轮询会替换整个消息数组导致丢失旧消息，改为合并逻辑
- 切换 Agent 后聊天框不自动滚到底部：异步加载期间 onScroll 误关了 auto-scroll
- Activity-only 消息无法触发加载更多历史：小字消息不够高、容器无滚动条，onScroll 永远不触发
- GeminiRAGTrigger 6 处调用传了多余的 `user_id` 参数，导致 `upload_file_to_store()` 报错
- `send_message_to_user_directly` 多次调用只保存首/末条：现在所有调用内容拼接保存
- Claude Code CLI agent loop 无超时：添加 120s 空闲超时，避免无限挂起
- Docker 容器启动失败（compose project name 变更时）：检测已有容器并 `docker start`
- API 错误（rate limit、auth）不可见：前端展示红色错误消息
- Module decision LLM 错误不可见：前端展示琥珀色警告
- `.env` API key 无法覆盖 shell 环境变量：修复优先级
- Deep research 只展示中间回复 "give me a moment"：改为展示最终结论性回复
- Agent SDK 无上限循环：添加 max_turns=100 安全限制
- 进程清理：wrapper 脚本处理 SIGTERM/SIGINT/SIGHUP，control.sh 2s 后 SIGKILL fallback
- MCP server 初始化时未正确 kill 旧进程
- Module decision 默认 skip 修复
- Bootstrap 自动删除修复
- Job `next_run_time` 时区 bug
- chatStore thinking fallback bug（错误地把 currentAssistantMessage 当 thinking）
- TS target 升级到 ES2023 以支持 `findLast`
- 仓库重命名 NexusAgent → NarraNexus，全局引用更新
- Channel prompt 强化，减少 agent 过度通信
- Desktop updater repo 地址、lint 错误修复，版本升级 1.0.1
- run.sh 自动 clone 和配置 NexusMatrix

## New Features

- **Unified Chat Timeline**：历史消息 + 实时消息合并为单一时间线，移除 "History Above" 分隔符，无限滚动加载
- **Multi-Agent Concurrent Chat**：多个 Agent 同时对话，chatStore 重构为 per-agent session map，WebSocketManager 单例
- **Agent Inbox Load-All**：Agent Inbox 分页加载全部消息，绕过 NexusMatrix 500 条上限
- **Event-Log Detail API**：`GET /event-log/{event_id}` 按需加载 thinking + tool calls，MessageBubble 懒加载展开/折叠
- **Matrix Communication Module**：MatrixModule MCP tools（发送/接收消息、创建/加入房间、agent 发现）
- **Matrix Trigger Rewrite**：per-room 消息批处理、持久化事件去重、@mention 过滤、自适应轮询
- **ClawHub Skill Installation**：自然语言描述需求 → LLM 搜索 ClawHub 匹配技能，chat-based 安装流程
- **Skill Env Config Management**：技能声明所需环境变量，用户通过 UI 配置
- **Skill Module MCP Tools**：`skill_save_config`、`skill_list_required_env`、`skill_save_study_summary`
- **Social Network MCP Tools**：`delete_entity`、`create_agent`
- **Matrix Module MCP Tools**：`leave_room`、`kick_from_room`
- **LLM API Cost Tracking**：cost_records 表 + API + CostPopover 前端组件
- **Channel Abstraction Layer**：ChannelContextBuilderBase、ChannelSenderRegistry、ChannelTag 多通道路由
- **Activity Records**：后台任务不回复时生成轻量 activity 记录，前端渲染为小字居中文本
- **Model Override**：`llm_function()` 支持 `model` 参数覆盖默认模型
- **Skip Module Decision**：`skip_module_decision_llm` 配置项跳过 LLM instance decision，节省 ~2.5-3s/turn
- **Background Notification**：非活跃 Agent 完成后 toast + badge 通知
- **MessageBubble**：copy Markdown / download .md 按钮
- **Auto Schema Sync**：启动时自动应用安全的 schema 变更（ADD COLUMN、ADD INDEX）
- **Desktop**：Synapse Matrix homeserver Docker 集成、auto-updater、CI/CD workflows

## Refactoring & DevOps

- Backend routes 拆分：agents.py (1850 行) → 6 个子模块
- Module 代码拆分：JobModule、SocialNetworkModule、ChatModule、RAG Module 各自抽取 MCP tools
- Frontend 组件抽取：AgentList、EntityCard、KPICard、SkillCard、InstallDialog、StatusDistributionBar
- run.sh `do_update` 简化为只拉代码，依赖更新交给 install
- Desktop 版本升级 1.0.1 → 1.0.2 → 1.0.3
- README 更新：截图、UI 指南、功能展示、v0.2.0 changelog
