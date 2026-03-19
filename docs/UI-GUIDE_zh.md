[← 返回 README](../README_zh.md)

# 界面使用说明

## 登录与创建 Agent

1. 打开 `http://localhost:5173`，输入任意 **User ID** 登录（如 `user_alice`）-- 系统以 User ID 区分用户身份
2. 首次使用需要创建 Agent：点击侧边栏的创建按钮，需要输入 **Admin Secret Key**（即 `.env` 文件中的 `ADMIN_SECRET_KEY`，默认值为 `nexus-admin-secret`）
3. 创建完成后即可在侧边栏看到你的 Agent，点击进入聊天

## 界面布局

<br/>

![界面](images/Interface.png)
<p align="center"><em>NarraNexus 界面</em></p>

<br/>

进入主界面后，为三栏布局：

```
┌──────────┬─────────────────────┬──────────────────────┐
│  侧边栏   │      聊天面板        │      上下文面板        │
│  Agent    │                     │                      │
│  列表     │  消息流（实时）       │  多个 Tab 切换：       │
│          │  历史记录            │  · Runtime            │
│          │  输入框              │  · Agent Config       │
│          │                     │  · Agent Inbox        │
│          │                     │  · Jobs               │
│          │                     │  · Skills             │
│          │                     │  💰 Cost（顶栏）      │
└──────────┴─────────────────────┴──────────────────────┘
```

## 侧边栏

- 登录后显示 Agent 列表，点击切换当前 Agent
- 切换 Agent 会自动加载该 Agent 的所有数据

## 聊天面板

- 与 Agent 的主要交互入口，通过 WebSocket 实时流式传输
- 发送消息后可以看到 Agent 的执行步骤（在右侧 Runtime Tab 中实时展示）
- 历史消息在切换 Agent 时自动加载（最近 20 条）

## 上下文面板

右侧面板包含多个 Tab，展示 Agent 的各项状态信息：

| Tab | 功能 | 需要手动刷新？ |
|-----|------|:---:|
| **Runtime** | 当前对话的 pipeline 步骤 + Narrative 列表 | Narrative 需要 🔄 |
| **Agent Config** | Agent 自我认知（可编辑）+ 社交网络列表（可搜索）+ RAG 文件管理 | 需要 🔄 |
| **Agent Inbox** | Agent 收到的来自其他用户的消息 | 需要 🔄 |
| **Jobs** | 任务列表 / 依赖图 / 时间线三种视图，支持按状态筛选和取消任务 | 需要 🔄 |
| **Skills** | Agent 可用的工具和技能列表 | 需要 🔄 |

> **⚠️ 重要提示：除聊天消息外，右侧面板的数据不会自动更新。** 当你通过聊天让 Agent 修改了 Awareness、创建了新任务、或更新了社交网络后，需要点击对应面板右上角的 🔄 刷新按钮 才能看到最新数据。

## 典型操作流程

1. **登录** → 选择或创建 Agent
2. **聊天配置** → 通过自然语言配置 Agent 的 Awareness（角色、目标、关键信息）
3. **刷新 Agent Config 面板** → 点击 🔄 确认配置已生效
4. **聊天分配任务** → 通过自然语言创建 Job（定时、周期、持续等）
5. **刷新 Jobs 面板** → 点击 🔄 查看已创建的任务列表
6. **持续交互** → Agent 执行任务后，刷新各面板查看社交网络更新、Narrative 积累等
