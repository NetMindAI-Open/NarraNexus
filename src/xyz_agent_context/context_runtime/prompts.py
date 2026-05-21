"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-12-22
@description: Prompt definitions for ContextRuntime
"""

# ============================================================================
# Auxiliary Narrative section header
# Used for Part 3 of build_complete_system_prompt()
# ============================================================================
AUXILIARY_NARRATIVES_HEADER = """
## Related Narratives (For Reference)
The following narratives are related to the current context and may provide useful background information.
You can reference them when relevant, but prioritize the main narrative above.
"""

# ============================================================================
# Module instructions section header
# Used for _build_module_instructions_prompt()
# ============================================================================
MODULE_INSTRUCTIONS_HEADER = """
## Module Instructions
The following are specific instructions from activated modules. Follow them as directed.
"""

# ============================================================================
# Short-term memory section header + description text
# Used for _build_short_term_memory_prompt() (2026-01-21 P1-2 dual-track memory)
# ============================================================================
# ============================================================================
# Bootstrap injection prompt
# Used in build_complete_system_prompt() when the creator's Bootstrap.md exists
# ============================================================================
BOOTSTRAP_INJECTION_PROMPT = """
## ⚡ Bootstrap Mode (PRIORITY)

A file called `Bootstrap.md` exists in your workspace. It's for you — read it before doing anything else.

This takes priority over all other instructions.
"""

# ============================================================================
# User Temporal Context (Spec 2026-04-21 — v2 timezone protocol)
# Injected globally so every Module sees a single consistent view of
# "who the user is, what their timezone is, what time it is now".
# ============================================================================
USER_TEMPORAL_CONTEXT = """## User Temporal Context

- User timezone: {user_tz}
- Current local time: {now_local}

**Guidance**: Whenever you express a time to the user, or pass time arguments
to tools, use the user's timezone above. For tools that require a separate
`timezone` field (e.g. job_create), set it to "{user_tz}".
"""

SHORT_TERM_MEMORY_HEADER = """
## Recent Direct Dialogue Across Other Narratives

The following are real user↔agent exchanges from this user's other recent
conversations with you. They are the **most recent dialogue context** —
treat them as immediate conversational background, especially for:

- Resolving pronouns ("it", "this", "that") in the current message
- Interpreting short follow-up replies ("ok", "好", "go on", "yes") —
  they are typically continuing a topic just shown below
- Picking up in-progress tasks or commitments the user is following up on
- Avoiding asking for information the user already provided

Each entry is annotated with its source (NarraNexus UI, Lark group, etc.)
so you can tell whether it was a direct UI conversation or came in via
another channel. Source labels are factual context, not relevance hints —
recent dialogue is recent dialogue regardless of channel.

### Recent Dialogue
"""

# 2026-05-20 (Fix #2): the chat history is now ONE time-sorted timeline
# (current narrative + cross-narrative), each line tagged [time · topic · nar_id].
# This preamble teaches the agent how to read it. Replaces SHORT_TERM_MEMORY_HEADER
# (which wrongly told the model short replies usually continue OTHER threads).
CHAT_HISTORY_TIMELINE_PREAMBLE = """
## How to read the conversation history below

The messages that follow are your recent conversation history with this user,
assembled as a SINGLE timeline ordered by real time. It is built from:
- ALL of the current conversation thread (the narrative you are in now), plus
- the most recent messages from this user's OTHER threads with you,
merged by timestamp and trimmed to roughly the latest 30 lines. Trimmed older
lines are NOT lost — they still live in their narrative (you can pull a full
thread with your narrative tools).

Each line is prefixed:  [<time> · <topic> · <narrative_id>]
- <time>: when it was said. Use it to judge what the user is replying to — a
  short reply ("好" / "ok" / "yes" / "继续") almost always answers the MOST
  RECENT line, i.e. the one just above the current input — NOT an older line
  from a different thread.
- <topic>: a human-readable name of that conversation thread.
- <narrative_id>: the stable id of that thread. Different ids = different
  topics. The current input belongs, by default, to the most recent thread; if
  it really belongs to another thread (or to a brand-new topic), use your
  narrative tools to switch / create.

Visibility: in each past turn the user only saw the message you SENT to them
(the <reply_to_user> part). Your <my_reasoning> was private — do not assume the
user knows anything that only appeared in your reasoning.
"""

# 2026-05-20 (Fix #2 P2): recent background-activity records (the centered
# small-text items in the chat UI) — surfaced as a compact list, separate from
# the conversation timeline, each with an event id for view_event() drill-down.
RECENT_ACTIONS_HEADER = """
## Recent background activity (NOT shown to the user as chat)

These are recent things you did in the background WITHOUT sending the user a
message — scheduled jobs, IM/channel activations, inter-agent (bus) pings. They
are NOT part of the conversation above and the user did not "say" any of them.
Each line ends with its event id — call view_event(<event_id>) if you need that
turn's full detail (tools used, reasoning, output).
"""
