# Telegram Bot Integration

NexusAgent supports Telegram as a chat channel. Once configured, users can interact
with their agent directly from the Telegram app — the same conversation history
and memory are shared with the web interface.

---

## Prerequisites

### Part 1 — During Installation (`run.sh`)

When running `bash run.sh` for the first time, you will be prompted to configure
the Telegram Bot. You only need your **Bot Token** at this stage:

1. Open Telegram and search for **[@BotFather](https://t.me/BotFather)**
2. Send `/newbot` and follow the prompts to name your bot
3. Copy the **Bot Token** (format: `1234567890:AAEjT...`)
4. Enter the token when `run.sh` asks: `Enable Telegram Bot? [y/N]:`

> **Note:** The Agent ID cannot be configured during installation because no agent
> exists yet. You must complete Part 2 after creating your first agent.

### Part 2 — Manual Configuration (After Creating an Agent)

Once your agent is running:

1. Open the NexusAgent panel, navigate to your agent, and copy the Agent ID
   (format: `agent_xxxxxxxx`)
2. Add it to your `.env` file:

```env
TELEGRAM_BOT_TOKEN=1234567890:AAEjT...
TELEGRAM_AGENT_ID=agent_xxxxxxxx
```

3. Restart the Telegram Bot process for the change to take effect.

---

## Starting the Bot

Run `bash run.sh` — the bot starts automatically as a 5th background process
alongside the other services once both `TELEGRAM_BOT_TOKEN` and `TELEGRAM_AGENT_ID`
are set in `.env`.

You should see in the logs:

```
============================================================
Starting Telegram Bot (Long Polling mode)...
  Agent ID: agent_xxxxxxxx
============================================================
Telegram Bot is ready, waiting for messages...
```

---

## Usage

Open Telegram, find your bot, and send any text message:

```
You:  What's on my schedule today?
Bot:  You have 3 meetings: standup at 9am, design review at 2pm,
      and a 1:1 with your manager at 4pm.

You:  Add a reminder to review the Q1 report on Friday at 5pm
Bot:  Done. I'll remind you on Friday at 17:00.

You:  What did we discuss yesterday?
Bot:  Yesterday we talked about the product roadmap and you asked me
      to track the API integration task.
```

Each Telegram user ID is treated as a separate user — multiple people can
interact with the same agent independently, each with their own conversation
history.

---

## Architecture

```
Telegram App
    │  (Long Polling)
    ▼
telegram_bot.py  ──►  AgentRuntime.run()  ──►  Claude (via claude CLI)
                              │
                              ▼
                      MySQL + Markdown memory
                      (shared with Web UI)
```

- **Transport**: Long Polling — no public URL or webhook required
- **Auth**: Reuses the existing `claude` CLI authentication (no separate API key)
- **Memory**: Shared with the web interface; conversation history persists across restarts
- **Concurrency**: Each message is handled asynchronously; multiple users are supported

---