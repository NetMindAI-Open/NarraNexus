---
code_file: src/xyz_agent_context/module/lark_module/lark_context_builder.py
stub: false
last_verified: 2026-07-29
---

## 2026-07-29 — group rooms get a read-the-history instruction

`_GROUP_ROOM_INSTRUCTION` is appended to `reply_instruction` when
`chat_type == "group"`. By the time this runs, [[lark_trigger]] has
already established the bot was @-mentioned — so someone deliberately
pulled it into a conversation mid-flight, and the pre-fetched history
window is both capped and frequently missing the exchange that prompted
the mention. The instruction tells the agent to widen its own view
(`im +chat-messages-list`, then `im +messages-search` when the thread
points further back) BEFORE answering, and to say what it could not read
rather than guess when a history call fails on permissions.

Direct messages deliberately do not get this — there is no third-party
context to reconstruct, and the extra text would only dilute the prompt.
## Why it exists

Builds execution context for Lark-triggered messages by implementing
`ChannelContextBuilderBase`.  Fetches conversation history and maps
Lark-specific fields to the normalized format the runtime expects.

## Design decisions

- **Inherits `ChannelContextBuilderBase`** — same pattern as other
  channel integrations (e.g., LINE, Discord).  Implements three
  methods: `get_message_info`, `get_conversation_history`,
  `get_room_members`.
- **`get_room_members` returns `[]`** — Lark CLI has no
  `+chat-members` shortcut yet.  Can be implemented via the API layer
  when needed.
- **Content JSON unwrapping** — Lark CLI may return message content
  as a JSON string `{"text": "hi"}`; the builder extracts the `text`
  field transparently.

## Upstream / downstream

- **Upstream**: `lark_trigger.py` (`_build_and_run_agent`).
- **Downstream**: `LarkCLIClient.list_chat_messages`,
  `ChannelContextBuilderBase.build_prompt`.

## Gotchas

- **`send_tool_name` is `"lark_cli"`** (V2). The base class template
  uses `{reply_instruction}` which this builder sets to a specific
  `lark_cli` call example. If you change the V2 tool signature, update
  the `reply_instruction` string here.
- **`reply_instruction` override** — unlike other channels that use the
  default `"use the {tool} tool with room_id={id}"`, Lark provides
  an explicit CLI command example because `lark_cli` takes a command
  string, not structured parameters.
- **`--markdown` is the default reply mode** — lark-cli auto-wraps
  `--markdown` content into Lark's post format so headings, bold,
  bullets and line breaks render as rich text in the chat bubble.
  Using `--text` sends the raw string as-is, which meant earlier
  versions leaked literal `**bold**` / backslash-n into user-facing
  replies when the agent produced markdown-shaped output. `--text`
  remains in the instruction as an escape hatch for code blocks /
  ASCII art where exact-verbatim layout matters.
