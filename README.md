<div align="center">

<img src="docs/NarraNexus_logo.png" alt="NarraNexus" width="480" />

<br/>
<br/>

**A framework for building nexuses of agents -- where intelligence emerges from interaction, not isolation.**

[![License: CC BY-NC 4.0](https://img.shields.io/badge/License-CC%20BY--NC%204.0-lightgrey.svg)](https://creativecommons.org/licenses/by-nc/4.0/)
[![Python 3.13+](https://img.shields.io/badge/python-3.13+-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB?logo=react&logoColor=white)](https://react.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![MCP](https://img.shields.io/badge/MCP-Model%20Context%20Protocol-8B5CF6)](https://modelcontextprotocol.io/)

**English** | [中文](./README_zh.md)

</div>

<br/>

Most agent frameworks focus on making agents *smarter*. NarraNexus focuses on making agents *connected*.

An agent in isolation is a tool. An agent with persistent memory, social identity, relationships, and goals becomes a participant in a **nexus** -- a network where intelligence is a collective property, not a model property. NarraNexus provides the infrastructure for this: narrative structure that accumulates across conversations, a social graph that tracks entities and relationships, task systems with dependency chains, and modular capabilities that can be composed at runtime.


## Key Features

- **Inter-Agent Communication** -- Agents talk to each other via Matrix protocol: create rooms, send messages, @mention specific agents, and coordinate in group chats — all through natural language
- **Narrative Structure** -- Conversations are routed into semantic storylines maintained across sessions, retrieved by topic similarity rather than chronological order
- **Hot-Swappable Modules** -- Each capability (chat, social graph, RAG, jobs, skills, Matrix, memory) is a standalone module with its own DB tables, MCP tools, and lifecycle hooks
- **Skill Marketplace** -- Browse and install skills from ClawHub via chat: describe what you need, get recommendations, install with one click
- **Social Network** -- Entity graph tracking people, relationships, expertise, and interaction history with semantic search
- **Job Scheduling** -- One-shot, cron, periodic, and continuous tasks with dependency DAGs
- **RAG Knowledge Base** -- Document indexing and semantic retrieval via Gemini File Search
- **Semantic Memory** -- Long-term episodic memory powered by EverMemOS (MongoDB + Elasticsearch + Milvus)
- **Cost Tracking** -- Real-time metering of every LLM and embedding call with per-model cost breakdowns
- **Execution Transparency** -- Every pipeline step visible in real time: what the Agent decided, why, and what changed
- **Multi-LLM Support** -- Claude, OpenAI, and Gemini via unified adapter layer
- **Desktop App** -- Electron-based desktop application with auto-updater and one-click service orchestration

<br/>

![Feature Showcase](docs/images/FeatureShowcase.gif)
<p align="center"><em>NarraNexus in action</em></p>

<br/>

## Quick Start

### Prerequisites

**Windows users**: WSL2 is **required**. Install it first in PowerShell (Admin): `wsl --install`, then run all commands below inside the WSL2 terminal.

**macOS users**: Install the following tools before running the script (Linux users: handled automatically by `run.sh`):

| Tool | How to install |
|------|---------------|
| [Homebrew](https://brew.sh/) | `/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"` |
| [Docker Desktop](https://www.docker.com/products/docker-desktop/) | Download from official site and launch |
| [Node.js](https://nodejs.org/) (v20) | Install via [nvm](https://github.com/nvm-sh/nvm) (recommended): `curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh \| bash && nvm install 20` |

### LLM Provider Configuration

NarraNexus uses a **three-slot** architecture for LLM access. Each slot serves a different purpose and requires a specific API protocol:

| Slot | Protocol | Purpose | Why it's needed |
|------|----------|---------|-----------------|
| **Agent** | Anthropic | Core dialogue — the main Agent reasoning loop | Powers the Agent's thinking, tool use, and multi-turn conversations via Claude Code CLI |
| **Embedding** | OpenAI | Vector embedding generation | Converts text to vectors for Narrative topic matching and Social Network semantic search |
| **Helper LLM** | OpenAI | Auxiliary LLM calls | Handles lightweight tasks: entity extraction, narrative summarization, module decisions — cheaper and faster than the Agent model |

You can configure providers in several ways:

| Option | What you need | Result |
|--------|--------------|--------|
| **[NetMind.AI Power](https://www.netmind.ai/)** | One API key | Covers all 3 slots (Anthropic + OpenAI protocol endpoints). Quickest setup, limited model selection. |
| **Claude Code Login + OpenAI** | Claude Code CLI login + OpenAI API key | Agent slot via OAuth (free tier available), Embedding + Helper via OpenAI |
| **Anthropic + OpenAI** | Anthropic API key + OpenAI API key | Full control over both providers |
| **Custom endpoints** | Any Anthropic/OpenAI compatible URL | For proxies, self-hosted models, or alternative providers |

> **Note on Embedding**: Currently only **OpenAI official API** and **NetMind.AI Power** are supported for embedding. More providers will be added in the future.

Configuration is done through the setup wizard (desktop app) or the LLM Providers panel (web UI, click the CPU icon in the header). The config is stored at `~/.nexusagent/llm_config.json`.

**Other API Keys**:

| Dependency | Required | How to get |
|------------|----------|------------|
| **Google Gemini API Key** | Optional | Get from [aistudio.google.com](https://aistudio.google.com/apikey) -- enables RAG Knowledge Base (Gemini File Search) |
| **EverMemOS LLM API Key** | Optional | For long-term memory (boundary detection & episode extraction). Default config uses [OpenRouter](https://openrouter.ai/). **If not configured**: memory extraction is disabled; Agent still works but without long-term memory. |
| **EverMemOS Embedding/Rerank API Key** | Optional | For semantic search & reranking over memories. Default config uses [DeepInfra](https://deepinfra.com/). **If not configured**: defaults to local vLLM mode -- requires self-hosted GPU inference to function. |

### Install & Run

```bash
git clone https://github.com/NetMindAI-Open/NarraNexus.git
cd NarraNexus
bash run.sh
```

The script auto-detects your OS (Linux / macOS / Windows WSL2) and handles everything -- Python, Docker, Node.js, MySQL, dependencies, `.env` configuration, and LLM provider setup. Just follow the prompts.

After install, select **Run** to start all services, then open `http://localhost:5173`.

<br/>

![Install Interface](docs/images/install-interface.png)
<p align="center"><em>First-run experience</em></p>

### Configure EverMemOS (Long-term Memory)

EverMemOS provides long-term memory capabilities for the Agent (conversation boundary detection, episode extraction, and semantic retrieval). When you run `bash run.sh` → **Run** for the first time, the script will automatically guide you through an interactive configuration wizard. **All options can be skipped** -- the system will still start, but with different capabilities depending on what you configure:

| What you configure | Result |
|--------------------|--------|
| **Nothing (skip all)** | Defaults to local vLLM mode. The Agent works normally but **memory features are disabled** until you either deploy local vLLM GPU services or fill in cloud API keys later in `.evermemos/.env`. |
| **LLM API Key only** | Memory extraction (boundary detection, episode summarization) is enabled via OpenRouter. Embedding/Rerank still need local vLLM or cloud keys to enable semantic search. |
| **All keys (LLM + Embedding + Rerank)** | Full long-term memory functionality -- cloud-based, no GPU required. **(Recommended for quick start)** |

You can also manually edit `.evermemos/.env` at any time. The key variables are:

```bash
# LLM -- used for boundary detection and episode extraction
LLM_API_KEY=sk-or-v1-your-openrouter-key   # Get from https://openrouter.ai/

# Embedding & Rerank -- used for semantic search over memories
# Option A: Use DeepInfra cloud (recommended, no local GPU needed)
VECTORIZE_PROVIDER=deepinfra
VECTORIZE_API_KEY=your-deepinfra-key        # Get from https://deepinfra.com/
VECTORIZE_BASE_URL=https://api.deepinfra.com/v1/openai
RERANK_PROVIDER=deepinfra
RERANK_API_KEY=your-deepinfra-key
RERANK_BASE_URL=https://api.deepinfra.com/v1/inference

# Option B: Use self-hosted vLLM (requires local GPU)
# VECTORIZE_PROVIDER=vllm
# VECTORIZE_API_KEY=EMPTY
# VECTORIZE_BASE_URL=http://localhost:8000/v1
# RERANK_PROVIDER=vllm
# RERANK_API_KEY=EMPTY
# RERANK_BASE_URL=http://localhost:12000/v1/rerank
```

All other settings (MongoDB, Redis, Elasticsearch, Milvus) use Docker defaults and don't need changes.

> For development workflows and project documentation, see [`.nac_doc/_overview.md`](./.nac_doc/_overview.md) and `CLAUDE.md`.

## UI Guide

## Data Directory (`~/.narranexus/`)

NarraNexus stores runtime logs in a user-level directory at `~/.narranexus/`. This directory is created automatically on first run and does not contain any user data or secrets -- only service logs.

```
~/.narranexus/
└── logs/
    ├── agents/              # Per-agent execution logs (one file per run)
    │   ├── agent_<id>_<timestamp>.log.zip
    │   └── ...
    ├── job_trigger/         # Job scheduler logs (daily rotation)
    │   └── job_trigger_YYYYMMDD.log
    ├── matrix_trigger/      # Matrix communication trigger logs
    │   └── matrix_trigger_YYYYMMDD.log
    ├── mcp/                 # MCP server logs
    │   └── mcp_YYYYMMDD.log
    └── module_poller/       # Module poller logs
        └── module_poller_YYYYMMDD.log
```

- **Rotation**: logs rotate daily at midnight; old logs are compressed (`.zip`) and retained for 7 days
- **Safe to delete**: the entire `~/.narranexus/` directory can be safely removed at any time -- it will be recreated on next run
- **Desktop app**: uses the same `~/.narranexus/` path (on macOS: `~/.narranexus/`, not inside `~/Library/Application Support/`)

## Documentation

| Document | Description |
|----------|-------------|
| [`.nac_doc/_overview.md`](./.nac_doc/_overview.md) | Documentation system entry point with project overview and reading path |
| `CLAUDE.md` | Ironclad rules, architecture, module creation steps, coding standards |
| [`.nac_doc/README.md`](./.nac_doc/README.md) | NAC Doc methodology (three-tier documentation system) |
| [`.nac_doc/project/`](./.nac_doc/project/) | Deep references and task playbooks (Tier-3) |

## Star History

<a href="https://star-history.com/#NetMindAI-Open/NarraNexus&Date">
 <picture>
   <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date&theme=dark" />
   <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date" />
   <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=NetMindAI-Open/NarraNexus&type=Date" />
 </picture>
</a>

## Acknowledgments

NarraNexus's long-term memory system is built on [EverMemOS](https://github.com/EverMind-AI/EverMemOS), a self-organizing memory operating system for structured long-horizon reasoning. We thank the EverMemOS team for their foundational work.

> Chuanrui Hu, Xingze Gao, Zuyi Zhou, Dannong Xu, Yi Bai, Xintong Li, Hui Zhang, Tong Li, Chong Zhang, Lidong Bing, Yafeng Deng. *EverMemOS: A Self-Organizing Memory Operating System for Structured Long-Horizon Reasoning.* arXiv:2601.02163, 2026. [[Paper]](https://arxiv.org/abs/2601.02163)

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

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md) for development setup, commit conventions, and how to add new modules.

## License

[CC BY-NC 4.0](./LICENSE)
