<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="docs/images/NarraNexusLogo_v2/narra-nexus-logo-text-dark-mode.svg">
  <img src="docs/images/NarraNexusLogo_v2/narra-nexus-logo-text-light-mode.svg" alt="NarraNexus" width="480" />
</picture>

<br/>
<br/>

# 让长期存在的 Agent 像同事一样记得、修正、协作

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-lightgrey.svg)](https://www.apache.org/licenses/LICENSE-2.0)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://www.narra.nexus/docs/getting-started/quick-start)
[![WeChat](https://img.shields.io/badge/WeChat-Join-07C160)](https://wechat-group-qr.narranexus.workers.dev/)
[![Discord](https://img.shields.io/badge/Discord-Join-5865F2)](https://discord.gg/ReCMd6a2wf)

[English](./README.md) | **中文**

<br/>
<sub>遇到 bug 或需要帮助？· <a href="https://github.com/NetMindAI-Open/NarraNexus/issues/new/choose">提 issue</a> · <a href="https://github.com/NetMindAI-Open/NarraNexus/discussions">讨论区</a></sub>

</div>

---

<p align="center">
  <em>NarraNexus 是一个赋予 Agent 生命力、帮助用户打造“一人公司”的长期 AI 团队平台。</em>
</p>

<p align="center">
  <img src="docs/images/hero-intro.gif" alt="90 秒看完 NarraNexus：安装、核心理念、几个 template 速览。" width="760" />
</p>

---

## NarraNexus 是什么

NarraNexus 是一个赋予 Agent 生命力、帮助用户打造“一人公司”的长期 AI 团队平台。

它不只是让多个 Agent 一起聊天或执行任务，而是让每个 Agent 都拥有持续存在的身份、会演化的长期记忆、独立工具和稳定的社交关系。多个 Agent 可以通过 MessageBus 协议互相 @mention、创建房间、群聊、分工和交接任务。

你管理的不再是一组用完即走的临时助手，而是一支能够记住过去、理解彼此、积累经验，并持续参与真实工作场景的 AI 团队。它们可以承担产品、研发、运营、研究等不同角色，帮助一个人逐步搭建并运营自己的“一人公司”。

| 维度 | OpenClaw | AionUI / OpenAgent | NarraNexus |
|---|---|---|---|
| 产品定位 | 面向个人的自托管执行型 Agent，强调自动化、渠道接入和本地控制 | AionUI 偏多 Agent Cowork 工作台；OpenAgent 偏自托管个人 Agent 平台 | 赋予 Agent 生命力，创建、运行和管理长期工作的 AI 团队 |
| Agent 组织方式 | 多个相互隔离的 Agent 实例，各自拥有 workspace、session、persona 和工具 | 统一管理或并行运行多个 Agent，重点是任务分派和使用体验 | Agent 不是临时实例，而是长期存在的团队成员，拥有固定身份、职责、关系和协作边界 |
| 记忆与身份 | 通过文件、会话记录和 memory search 维持上下文与 persona | 主要依赖会话历史、RAG、知识库及底层 Agent 自身能力 | Narrative + Awareness 让 Agent 记住经历、维持身份，并让认知随时间持续演化 |
| 多 Agent 协作 | 以 Agent 隔离、路由和扩展式协作为主 | 以统一调用、并行执行、任务分派和运行监控为主 | 通过 MessageBus、Job 和 Social Network，让 Agent 形成可持续的分工、关系和团队经验 |
| 核心差异 | 更像多个独立运行的个人 Agent | 更像统一管理多个 Agent 的工作台或网关 | 不只是把多个 Agent 放在一起，而是让它们拥有记忆、身份、关系和成长能力，成为一支有生命力的 AI 团队 |

---

## 为什么需要 NarraNexus

大多数 Agent 产品已经不只是一次性工具。OpenClaw 能长期运行、接入渠道并管理多个独立 Agent；Hermes 能跨会话保存记忆、学习用户并沉淀 Skills；AionUI、OpenAgent 等产品则让用户统一调用、并行运行和管理多个 Agent。

但它们解决的重点并不相同：OpenClaw 更偏个人 Agent 的运行、隔离和自动化；Hermes 更关注单个 Agent 如何从经验中成长；Agent IM 更关注如何在一个界面里调度和观察多个 Agent。它们能让 Agent 更强、更好用，却不一定把多个 Agent 建模成一支长期存在的团队。

当 Agent 要连续工作几天、几周，甚至成为固定岗位时，核心问题不再是“能不能完成这一轮任务”，而是它能不能像真实同事一样形成连续的自我：

- **知道自己是谁**：为谁工作、负责什么、有什么偏好和边界。
- **知道事情怎么变了**：旧结论被推翻后，不再继续误导判断。
- **知道和谁打过交道**：针对不同用户、客户、队友形成不同关系。
- **知道团队里谁知道什么**：经验能在权限和边界内传递给其他 Agent。

传统记忆往往只能保存更多内容，却难以处理身份连续性、时间变化、关系边界和团队经验。滑动窗口会忘，简单 RAG 会混入过期事实，固定 persona 只是静态设定，共享知识库也容易把协作变成无边界的信息池。

NarraNexus 要解决的，正是 Agent 的生命力问题：通过 Narrative、Awareness、Social Network 和 MessageBus，让 Agent 拥有持续身份、会演化的记忆、稳定关系和长期协作能力。

它不是再做一个更强的个人 Agent，也不是再做一个多 Agent 工作台，而是把 Agent 从“用完即走的工具”推进为“长期存在、能够成长和协作的 AI 团队成员”。

> 失忆的东西是工具。连续且会自我修正的记忆，才是生命的起点。

---

## 核心设计

### 1. Narrative 长期记忆

NarraNexus 不把聊天记录简单堆进一个向量库，而是把对话和事件组织成不同的 storyline。Agent 可以跨会话延续同一条故事线，知道任务做到哪、过去发生过什么、哪些判断已经不再成立。

记忆带有时间观。一个旧结论被新事实推翻后，不会被粗暴删除，也不会继续作为当前事实污染判断，而是被标记为失效并保留其历史来源。Agent 因此能表达“我曾经以为是这样，后来发现不是”。

### 2. Awareness 持久身份

长期 Agent 不能只靠一段开场 prompt 维持人格。NarraNexus 用 Awareness module 承载 Agent 的身份、职责、偏好和行为边界，让它跨会话记得“我是谁、我为谁工作、我该如何做判断”。

这让 Agent 更接近一个岗位，而不是一次 prompt 运行。

### 3. Social Network 关系记忆

真实工作不是只处理任务，还要处理人、组织、客户、项目和它们之间的关系。Social Network module 让 Agent 记住长期互动对象，并逐渐形成差异化的沟通方式。

同一句“帮我跟进一下”，对内部队友、外部客户、投资人、创作者，应该触发不同的语气、边界和行动路径。

<p align="center">
  <img src="docs/images/product-tour.gif" alt="界面速览：Narra 记忆（记忆时间轴）、Nexus 网络（关系图谱）、世界观（每个 Agent 眼中的你）三个核心界面。" width="820" />
</p>

### 4. 有边界的团队协作

NarraNexus 支持多 Agent 组成团队，但不是把所有信息倒进一个共享大脑。每条记忆都有作用域：Agent、用户、故事线、团队或全局共享。默认有墙，需要共享时才通过治理过的通道流动。

一个 Agent 验证过的方法可以被团队复用；一个 Agent 的私有策略不会因为“协作”而泄漏给不该知道的人。

### 5. 每个 Agent 独立 Skill 和 MCP 工具集

每个 Agent 都可以拥有自己的工具、Skill 和 MCP 服务。你可以按 Agent 安装能力，热插拔扩展，不需要为了一个新工具修改全局代码，也不会让所有 Agent 被迫共享同一套插件。

<p align="center">
  <img src="docs/images/agent-modules.gif" alt="每个 Agent 内的三个模块：记忆（storyline 卡片）、社交网络（联系人与紧密度）、技能（可热插拔的 Skill）。" width="900" />
</p>

---

## 适合什么场景

### 市场与竞品持续跟踪

一个人运营公司，很难每天同时跟进竞品更新、行业新闻、用户反馈和市场机会。普通 Agent 每次都像重新开始，容易重复汇报旧信息，甚至继续沿用已经失效的判断。

NarraNexus 可以让研究 Agent 沿着长期 storyline 持续监控，只保留当前有效的认知，并把重要变化同步给产品、运营和内容 Agent。

### 多岗位协同执行

一人公司最大的痛点不是没有想法，而是一个人要同时负责产品、研发、运营、销售和内容。

在 NarraNexus 中，不同 Agent 可以承担项目经理、研究员、开发者、内容编辑和增长运营等岗位，通过 MessageBus 沟通、分工和交接任务。你只需要确定目标和关键决策，不必反复向每个 Agent 补充相同背景。

### 客户与合作关系管理

客户需求、KOL 商单、渠道合作和社群沟通往往分散在不同对话中。时间一长，很容易忘记对方的背景、历史承诺、沟通偏好和当前进度。

NarraNexus 不只保存聊天记录，还会沉淀客户、合作方和项目之间的关系，让 Agent 在后续跟进时知道对方是谁、之前发生过什么，以及应该采用什么沟通方式。

### 项目长期推进

一人公司通常会同时推进产品开发、内容发布、客户交付和商业合作。普通 Agent 可以完成单个任务，却很难持续理解项目为什么这样做、哪些方案已经否决、下一步由谁负责。

NarraNexus 会将任务、记忆、关系和协作记录连接起来，让 Agent 跨天续跑项目，延续已有决策和进度，而不是每次重新梳理上下文。

### 经验沉淀与业务复制

一个人最容易遇到的问题，是所有经验都留在自己脑中：什么内容有效、客户为什么流失、哪种开发方案踩过坑，都难以稳定复用。

NarraNexus 让不同 Agent 保留自己的岗位经验，并在权限边界内传递给团队。随着业务持续运行，这支 AI 团队会逐步理解你的工作方式，减少重复试错，帮助你把个人经验转化为可复用的公司能力。

---

## 60 秒上手

NarraNexus 提供三种使用方式，选你顺手的一种。

### 云端注册

最快开始，带免费体验额度。

1. 打开 [agent.narra.nexus](https://agent.narra.nexus/login)
2. 注册账号
3. 选一个 template，开始使用

### macOS 桌面应用

桌面端自带 runtime，不用额外安装 Python、Node 或 Docker。

1. [下载 app](https://github.com/NetMindAI-Open/NarraNexus/releases/latest/download/NarraNexus.dmg)
2. 拖入 Applications 文件夹
3. 启动后选择一个 template

### 从源码运行

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

`run.sh` 会检测前置依赖并启动本地服务。完整安装步骤见 [Quick Start](https://www.narra.nexus/docs/getting-started/quick-start)。

> 本地运行和桌面端需要配置自己的 LLM API key。云端版本提供免费体验额度。

---

<a name="templates"></a>

## Reference Templates

### 金融市场晨报

适合每天要看市场的投资者和研究员。6 个 Agent 每天生成分析师级别的 HTML 简报，回答“今天市场在交易什么，我该进攻、防守还是观望”。

[narra.nexus/templates/financial-morning-briefing](https://www.narra.nexus/templates/financial-morning-briefing)

### KOL Assistant

适合接 sponsorship 的内容创作者。4 个 Agent 解析 sponsor 邮件、维护 CRM、跨平台监测品牌提及，让创作者少花时间处理收件箱。

[narra.nexus/templates/kol-assistant](https://www.narra.nexus/templates/kol-assistant)

### PM Bridge Bot

适合同时管理内部协作和外部客户沟通的团队。一个 Bot 维护内部专用和对客共享两套知识库，把聊天、文档、会议记录归档到正确范围。

[narra.nexus/templates/pm-bridge-bot](https://www.narra.nexus/templates/pm-bridge-bot)

更多 template 见 [narra.nexus/templates](https://www.narra.nexus/templates)。

---

## 诚实边界

- **Agent 不是一上来就 100 分**：它需要你的纠错和反馈，越用越合手。更像新同事入职，不是神谕机。
- **记忆不是越多越好**：NarraNexus 的重点不是无限保存，而是让记忆有身份、时间、作用域和可修正机制。
- **协作需要设计边界**：多 Agent 团队不是把所有信息混在一起。好的协作来自清楚的职责和受治理的共享。
- **本地运行需要 API key**：桌面端和源码运行使用你自己的 LLM API key。云端版本有免费体验额度。

---

## 社群

- 微信 WeChat：[扫码加入群聊](https://wechat-group-qr.narranexus.workers.dev/)
- Discord：[discord.gg/ReCMd6a2wf](https://discord.gg/ReCMd6a2wf)
- Twitter / X：[@NetMindAI](https://x.com/NetMindAI)
- 反馈：[GitHub Issues](https://github.com/NetMindAI-Open/NarraNexus/issues)
- 讨论：[GitHub Discussions](https://github.com/NetMindAI-Open/NarraNexus/discussions)

---

## 贡献与治理

NarraNexus 同时为人类贡献者和 AI Agent 贡献者设计。

- 新贡献者请看 [`CONTRIBUTING.md`](./CONTRIBUTING.md)
- AI 编码助手请先看 [`AGENTS.md`](./AGENTS.md) 或 [`CLAUDE.md`](./CLAUDE.md)
- 项目地图见 [`.mindflow/_overview.md`](./.mindflow/_overview.md)
- 治理与维护者团队见 [`GOVERNANCE.md`](./GOVERNANCE.md) 和 [`MAINTAINERS.md`](./MAINTAINERS.md)
- 社区准则见 [`CODE_OF_CONDUCT.md`](./CODE_OF_CONDUCT.md)
- 安全策略见 [`SECURITY.md`](./SECURITY.md)

---

## 许可证

[Apache 2.0](./LICENSE)
