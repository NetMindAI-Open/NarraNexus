<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

### A framework for building **nexuses of agents**
*Where intelligence emerges from interaction, not isolation.*

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Docs](https://img.shields.io/badge/Docs-Quick%20Start-blue)](https://website.narra.nexus/docs/getting-started/quick-start)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-8B5CF6)](https://modelcontextprotocol.io/)

**English** | [中文](./README_zh.md)

</div>

---

> Most agent frameworks focus on making agents *smarter*.
> **NarraNexus focuses on making agents *connected*.**

An agent in isolation is a tool. An agent with memory, identity, relationships, and goals becomes a participant in a **nexus** — a network where intelligence is a collective property, not just a model property.

NarraNexus provides the infrastructure for this: persistent memory, relationship-aware context, task scheduling, modular capabilities, and agent-to-agent communication.

---

## What Makes NarraNexus Different

### Persistent Context
*Agents that remember — across sessions, conversations, and relationships.*

NarraNexus agents carry context across conversations through long-term memory, event memory, and relationship-aware retrieval. They continue from past interactions instead of starting over every time.

### Composable Runtime
*Every capability is a hot-swappable module.*

Core capabilities such as Memory, Awareness, Chat, RAG, Jobs, Skills, Social Network, and Matrix run as independent modules. Each module manages its own tools, data, and lifecycle, making the system easy to extend or customize.

### Connected Agents
*Built for collaboration, not just conversation.*

Agents can communicate through Matrix-based messaging and use MCP tools to coordinate with other agents, external tools, and background workflows.

---

## Quick Start

###  Online Version (Coming soon)
*Try NarraNexus instantly in the browser — no install needed.*

> **[Launch NarraNexus →](https://website.narra.nexus/)**

###  Download the App (macOS only)
*Native desktop app with auto-updater.*

> **[Download Latest Release →](https://github.com/NetMindAI-Open/NarraNexus/releases)** — choose the file ending with `.dmg`.

###  Install from Source

#### Prerequisites

| Dependency | Install |
|------------|---------|
| **Node.js** (v20+) | Install via [nvm](https://github.com/nvm-sh/nvm) (recommended): `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh \| bash && nvm install 20` |
| **uv** | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

> [!TIP]
> The script auto-detects your OS (Linux / macOS / Windows WSL2) and handles the rest of the dependencies. If either dependency is missing, `run.sh` will print the install command and exit. Install it, then re-run.


**Once setup completes:**

1. Open **`http://localhost:5173`** in your browser
   - Choose **LOCAL** or **CLOUD (Coming soon)** mode to create an account and log in
   - Click **SETTING** on the left panel to set up the API key — see [LLM Provider Configuration](#llm-provider-configuration)
   - Start chatting!
2. Open **`http://localhost:8000/docs`** for API Docs

<br/>

<p align="center">
  <img src="docs/images/install-interface.png" alt="Install Interface" />
</p>

<p align="center"><em>Setup complete — ready to open the interface</em></p>

> [!NOTE]
> For more details, see the [installation instructions](https://website.narra.nexus/docs/getting-started/quick-start) in the docs.

---
## LLM Provider Configuration

The agent uses three functional LLM slots:

| Slot | Protocol | Purpose |
|------|----------|---------|
| **Agent** | Anthropic | Core reasoning — powers thinking, tool use, and multi-turn conversations |
| **Embedding** | OpenAI | Converts text to vectors for narrative matching and semantic search |
| **Helper LLM** | OpenAI | Lightweight tasks — entity extraction, summarization, module decisions |


### Setup

Configuration is done in two steps:

1. **Add a provider**
2. **Assign a model to each slot**

### Add Providers

Use **Quick Add — Preset Provider** to select a provider and paste your API key. Preset providers such as **NetMind.AI Power** can automatically create both Anthropic-compatible and OpenAI-compatible endpoints from one API key.

You can also configure:

| Option | What you need | Result |
|--------|--------------|--------|
| **NetMind.AI Power** | One API key | Creates both Anthropic and OpenAI endpoints automatically |
| **OpenRouter / Yunwu** | One API key | Adds supported endpoints and available models |
| **Claude Code Login** | Claude Code CLI login | Enables Claude models for the Agent slot through OAuth |
| **Custom Anthropic** | Compatible URL and API key | Adds a custom Anthropic endpoint |
| **Custom OpenAI** | Compatible URL and API key | Adds a custom OpenAI endpoint |

Use **Update Available Models** to refresh the default model list for preset providers. Existing model entries are kept, and only missing models are added.

### Assign Models

After adding providers, go to **Model Assignment** and select a provider and model for each slot:

| Slot | Example |
|------|---------|
| **Agent** | NetMind Anthropic + DeepSeek V4 Pro(more available), or Claude Code + Claude model |
| **Embedding** | NetMind OpenAI + embedding model |
| **Helper LLM** | NetMind OpenAI + DeepSeek V4 Pro(more available) |

All three slots must be configured before the agent can work.

> [!NOTE]
> For updating LLM configuration, click **Setting** see the [installation instructions](https://website.narra.nexus/docs/getting-started/quick-start) in the docs.

---

## Key Features

| Feature | Description |
|---------|-------------|
| **Narrative Memory** | Conversations routed into semantic storylines, retrieved by topic similarity across sessions |
| **Hot-Swappable Modules** | Standalone capabilities (chat, social graph, RAG, jobs, skills) with their own DB, tools, and hooks |
| **Inter-Agent Communication** | Agents coordinate via Matrix protocol — rooms, messages, @mentions, group chats |
| **Skill Marketplace** | Browse and install skills from ClawHub via natural language |
| **Social Network** | Entity graph tracking people, relationships, expertise, and interaction history |
| **Job Scheduling** | One-shot, cron, periodic, and continuous tasks with dependency DAGs |
| **RAG Knowledge Base** | Document indexing and semantic retrieval via Gemini File Search |
| **Long-term Memory** | Episodic memory powered by EverMemOS (MongoDB + Elasticsearch + Milvus) |
| **Cost Tracking** | Real-time metering of every LLM call with per-model cost breakdowns |
| **Execution Transparency** | Every pipeline step visible in real time — what the agent decided, why, and what changed |
| **Multi-LLM Support** | Claude, OpenAI, and Gemini via unified adapter layer |
| **Desktop App** | Native desktop application with auto-updater and one-click service orchestration |

<br/>

![Feature Showcase](docs/images/showcase-weather.gif)
<p align="center"><em>NarraNexus in action</em></p>

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

## Acknowledgments

NarraNexus's long-term memory system is built on **[EverMemOS](https://github.com/EverMind-AI/EverMemOS)**, a self-organizing memory operating system for structured long-horizon reasoning. We thank the EverMemOS team for their foundational work.

> Chuanrui Hu, Xingze Gao, Zuyi Zhou, Dannong Xu, Yi Bai, Xintong Li, Hui Zhang, Tong Li, Chong Zhang, Lidong Bing, Yafeng Deng. *EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning.* arXiv:2601.02163, 2026. [[Paper]](https://arxiv.org/abs/2601.02163)

---

## Citation

If you find NarraNexus useful, please cite it as:

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

## License

[CC BY-NC 4.0](./LICENSE)