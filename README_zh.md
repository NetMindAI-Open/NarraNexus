<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

### 构建 **Agent 连接网络** 的框架
*让智能从交互中涌现，而非孤立运行。*

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-8B5CF6)](https://modelcontextprotocol.io/)

[English](./README.md) | **中文**

</div>

---

> 大多数 Agent 框架的目标是让 Agent 更*聪明*。
> **NarraNexus 的目标是让 Agent 更*互联*。**

封闭的单个 Agent 只是工具。当 Agent 拥有持久记忆、社会身份、人际关系和目标时，它就成为**连接网络（Nexus）**中的参与者——在这个网络中，智能是集体属性，而非模型属性。

NarraNexus 为此提供基础设施：持久记忆、关系感知的上下文、任务调度、模块化能力，以及 Agent 间通信。

---

## NarraNexus 的独特之处

### 持久上下文
*能够记忆的 Agent——跨会话、跨对话、跨关系。*

NarraNexus Agent 通过长期记忆、事件记忆和关系感知检索在对话之间保持上下文。Agent 能从过去的交互中延续，而非每次从头开始。

### 可组合运行时
*每项能力都是可热插拔的模块。*

Memory、Awareness、Chat、RAG、Jobs、Skills、Social Network 和 Matrix 等核心能力作为独立模块运行。每个模块管理自己的工具、数据和生命周期，使系统易于扩展和定制。

### 互联 Agent
*为协作而生，不只是对话。*

Agent 通过基于 Matrix 协议的消息系统相互通信，并使用 MCP 工具与其他 Agent、外部工具和后台工作流协调。

---

## 快速开始

###  在线版本（即将推出）
*在浏览器中直接体验 NarraNexus，无需安装。*

> **[启动 NarraNexus →](https://website.narra.nexus/)**

###  下载桌面应用（仅限 macOS）
*原生桌面应用，支持自动更新。*

> **[下载最新版本 →](https://github.com/NetMindAI-Open/NarraNexus/releases)** — 选择以 `.dmg` 结尾的文件。

###  从源码安装

#### 前置依赖

| 依赖 | 安装方式 |
|------|---------|
| **Node.js** (v20+) | 推荐通过 [nvm](https://github.com/nvm-sh/nvm) 安装：`curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh \| bash && nvm install 20` |
| **uv** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

> [!TIP]
> 脚本会自动检测操作系统（Linux / macOS / Windows WSL2）并处理其余依赖。如果检测不到上述任一依赖，`run.sh` 会打印安装命令并退出，安装后重新运行即可。


**安装完成后：**

1. 在浏览器中打开 **`http://localhost:5173`**
   - 选择 **LOCAL** 或 **CLOUD（即将推出）** 模式注册账号并登录
   - 点击左侧面板的 **SETTING** 设置 API Key——详见 [LLM 供应商配置](#llm-供应商配置)
   - 开始聊天！
2. 打开 **`http://localhost:8000/docs`** 查看 API 文档

<br/>

<p align="center">
  <img src="docs/images/install-interface.png" alt="安装界面" />
</p>

<p align="center"><em>安装完成——准备打开界面</em></p>

> [!NOTE]
> 更多详情请参阅文档中的[安装说明](https://website.narra.nexus/docs/getting-started/quick-start)。

---

## LLM 供应商配置

Agent 使用三个功能性 LLM 槽位：

| 槽位 | 协议 | 用途 |
|------|------|------|
| **Agent** | Anthropic | 核心推理——驱动 Agent 的思考、工具调用和多轮对话 |
| **Embedding** | OpenAI | 将文本转换为向量，用于 Narrative 匹配和语义搜索 |
| **Helper LLM** | OpenAI | 轻量任务——实体提取、摘要生成、模块决策 |


### 配置步骤

配置分两步完成：

1. **添加供应商**
2. **为每个槽位分配模型**

### 添加供应商

使用 **Quick Add — Preset Provider**，选择供应商并粘贴 API Key。预设供应商（如 **NetMind.AI Power**）可以从一个 API Key 自动创建 Anthropic 兼容端点和 OpenAI 兼容端点。

也可以手动配置：

| 方式 | 你需要的 | 效果 |
|------|---------|------|
| **NetMind.AI Power** | 一个 API Key | 自动创建 Anthropic 和 OpenAI 两个端点 |
| **OpenRouter / 云雾** | 一个 API Key | 添加支持的端点和可用模型 |
| **Claude Code 登录** | Claude Code CLI 登录 | 通过 OAuth 为 Agent 槽位启用 Claude 模型 |
| **自定义 Anthropic** | 兼容 URL 和 API Key | 添加自定义 Anthropic 端点 |
| **自定义 OpenAI** | 兼容 URL 和 API Key | 添加自定义 OpenAI 端点 |

使用 **Update Available Models** 可刷新预设供应商的默认模型列表，已有模型条目保留，仅追加缺失模型。

### 分配模型

添加供应商后，进入 **Model Assignment**，为每个槽位选择供应商和模型：

| 槽位 | 示例 |
|------|------|
| **Agent** | NetMind Anthropic + DeepSeek V4 Pro（更多可选），或 Claude Code + Claude 模型 |
| **Embedding** | NetMind OpenAI + Embedding 模型 |
| **Helper LLM** | NetMind OpenAI + DeepSeek V4 Pro（更多可选） |

三个槽位必须全部配置，Agent 才能正常工作。

> [!NOTE]
> 如需更新 LLM 配置，点击 **Setting**，详见文档中的[安装说明](https://website.narra.nexus/docs/getting-started/quick-start)。

---

## 核心特性

| 特性 | 描述 |
|------|------|
| **叙事记忆** | 对话被路由到语义故事线中，按话题相似度跨会话检索 |
| **热插拔模块** | 独立能力模块（聊天、社交图谱、RAG、任务、技能），各自拥有数据库、工具和钩子 |
| **Agent 间通信** | Agent 通过 Matrix 协议协调——房间、消息、@提及、群聊 |
| **技能市场** | 通过自然语言从 ClawHub 浏览和安装技能 |
| **社交网络** | 实体图谱追踪人物、关系、专业领域和互动历史 |
| **任务调度** | 一次性、定时、周期和持续任务，支持依赖链（DAG） |
| **RAG 知识库** | 基于 Gemini File Search 的文档索引与语义检索 |
| **长期记忆** | 基于 EverMemOS（MongoDB + Elasticsearch + Milvus）的情景记忆 |
| **成本追踪** | 实时计量每次 LLM 调用，按模型分类展示费用明细 |
| **执行透明度** | 每个流水线步骤实时可见——Agent 做了什么决策、为什么、改变了什么 |
| **多 LLM 支持** | Claude、OpenAI 和 Gemini 通过统一适配层接入 |
| **桌面应用** | 原生桌面应用，支持自动更新和一键服务编排 |

<br/>

![功能展示](docs/images/showcase-weather.gif)
<p align="center"><em>NarraNexus 功能展示</em></p>

---

## Star History

<a href="https://star-history.com/#NetMindAI-Open/NarraNexus&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date" />
 </picture>
</a>

---

## 致谢

NarraNexus 的长期记忆系统基于 **[EverMemOS](https://github.com/EverMind-AI/EverMemOS)** 构建，这是一个用于结构化长程推理的自组织记忆操作系统。感谢 EverMemOS 团队的基础性工作。

> Chuanrui Hu, Xingze Gao, Zuyi Zhou, Dannong Xu, Yi Bai, Xintong Li, Hui Zhang, Tong Li, Chong Zhang, Lidong Bing, Yafeng Deng. *EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning.* arXiv:2601.02163, 2026. [[论文]](https://arxiv.org/abs/2601.02163)

---

## 引用

如果 NarraNexus 对你的工作有帮助，请引用：

```bibtex
@software{narranexus2026,
  title        = {NarraNexus: A Framework for Building Nexuses of Agents},
  author       = {NetMind.AI},
  year         = {2026},
  url          = {https://github.com/NetMindAI-Open/NarraNexus},
  license      = {CC-BY-NC-4.0}
}
```

---

## 许可证

[CC BY-NC 4.0](./LICENSE)
