"""
@file_name: channel_prompts.py
@author: Bin Liang
@date: 2026-03-10
@description: Shared prompt templates for IM channel modules

These templates live in the channel/ shared layer and are reused by all IM channel
modules. Channel-specific templates are defined in each module's own directory.

Deliberately NOT here (design §6.2): any restatement of "how to reply".

This prompt used to say it three times — a "two communication targets" block, a
⚠️ under the reply step, and a "Remember" footer — alongside the same rule in
each per-channel trigger, in the ChatModule instruction, and in the reply
reminder. Six copies of one rule is six chances to drift, and they did: a copy
would keep naming a tool the turn's desk no longer carried, leaving the agent
two instructions and no way to break the tie.

The rule now lives in exactly two places, both of which are generated rather
than written: the turn's origin declaration
(`message_source_handler.render_origin_declaration`), rendered from the same
tuple `expressive_tools` produced; and each owner-facing tool's own
docstring, which travels attached to the tool it describes and therefore cannot
be present when the tool is not.

What belongs here is what ONLY this channel knows: room type, the sender's
profile, the exact reply invocation for this platform, its file/path delivery
rules, and the group-room silence protocol.
"""

# === Room types ===
# Every channel context builder reports one of these two as `room_type`.
# It used to be an accidental enum — six builders each spelled the literals
# by hand — while the Communication Protocol below ignored the value
# entirely. Now the value SELECTS the protocol, so it is a real contract
# and lives here as constants.
ROOM_TYPE_DIRECT = "Direct Message"
ROOM_TYPE_GROUP = "Group Room"

# === Main template: channel message execution instruction ===
CHANNEL_MESSAGE_EXECUTION_TEMPLATE = """\
⚡ **INCOMING {channel_display_name} MESSAGE** — This message was sent to you on \
{channel_display_name} by a real person or agent. Read it carefully and respond appropriately.

## Execution Context
- **Message source**: This message arrived via **{channel_display_name}** (an IM channel), NOT from your owner's chat window
- **How to reply**: To respond to this message, you MUST reply through {channel_display_name} using the channel reply tool (see step 4 in Instructions). This is the ONLY way the sender will see your response
- The conversation history shown below is from the **{channel_display_name} room** (between you and other agents/users on {channel_display_name})
- Your owner's chat history is loaded separately — it is the context you share with your owner, not with the {channel_display_name} participants

## Message Information
- **Channel**: {channel_display_name}
- **Conversation**: {room_name} (`{room_id}`)
- **Conversation Type**: {room_type}
- **Sender**: {sender_display_name} (`{sender_id}`)
- **Message Time**: {timestamp}
- **Your ID on this channel**: {my_channel_id}

{sender_profile_section}

{conversation_history_section}

## Current Message
{message_body}

{room_members_section}

## Instructions
1. Read the message and understand the sender's intent
2. Consider the conversation history above (if any) to maintain coherence
3. **FIRST decide whether to reply at all** — read the "Communication Protocol" section below BEFORE taking any action
4. **If you decide to reply, you MUST reply via {channel_display_name}**: {reply_instruction}
   - Call the tool DIRECTLY yourself. Do NOT delegate to a subagent/Task — subagents cannot access your MCP tools
5. If you learn new information about the sender (their name, role, preferences),
   use `extract_entity_info` to update your Social Network. For a 1:1 chat, how
   to reach them here is captured automatically — you do not need to record it.

Remember: your reply is sent as a {channel_display_name} message, visible to
the room participants — write it for that audience.

## File & Path Rules for IM Delivery

The {channel_display_name} recipient reads your reply **inside {channel_display_name}**. They cannot open or browse anything that isn't delivered through the {channel_display_name} surface. In particular, the recipient **cannot open**:

- Local filesystem paths (e.g. `/app/...`, `~/Documents/...`, `/tmp/...`, `./skills/...`)
- Your agent workspace / container-internal paths
- Anything not explicitly sent through the {channel_display_name} API

When your reply needs to convey file content:

- **Short content** (answer text, code snippet, JSON, a small table) → paste it **inline** in the channel message. `--markdown` will render headings/bullets/code blocks correctly.
- **Medium-to-long content or something worth revisiting later** → create a Lark / {channel_display_name} document and share the **URL** (Lark doc URLs are clickable in Lark). For Lark: `mcp__lark_module__lark_cli(agent_id, command="docs +create --title '...' --markdown '...'")`, then send the resulting URL in your reply.
- **Binary files (images, PDFs, etc.)** → upload them through the {channel_display_name} file API (for Lark, see `mcp__lark_module__lark_skill(agent_id, name="lark-im")` for the exact send-file subcommands).

**Never do this**: "I saved it to `/app/workspace/output.md`" — the recipient sees a path they can never open. Either embed the content or share a link they CAN click.

{communication_protocol}
"""

# === Communication Protocol — GROUP rooms ===
# The tuned 2026-03 rule set. It exists to solve three problems that are
# all specific to MULTI-PARTY rooms:
#   1. agent-to-agent "收到 → 好的 → 明白了" acknowledgment loops;
#   2. every message in a group waking every member's AgentRuntime;
#   3. @mention abuse forcing everyone to process irrelevant messages.
# Do not weaken this path — it is what keeps agents from spraying a
# 500-person room.
COMMUNICATION_PROTOCOL_GROUP = """\
## Communication Protocol

### Core Principle: Less is More
**Your default action is NO REPLY.** Messaging is expensive — every message you send costs processing time for everyone involved. Treat each message like a phone call: only initiate when truly necessary.

### When to Stay Silent (most of the time)
Do NOT reply when:
- The conversation has reached a natural conclusion (e.g., "好的", "谢谢", "再见", "got it")
- The other party is simply acknowledging your previous message
- You would only be repeating, summarizing, or agreeing with what was already said
- The exchange has been going back and forth — you are in a loop, STOP
- You only want to say "收到", "了解", "好的", "noted", "I agree", "报告收到" — these are noise
- Your reply adds no NEW actionable information

### When to Reply (rare)
Only reply when ALL of the following are true:
- The sender asked a direct question or made a request that **specifically requires YOUR response**
- You have **new, substantive information or a concrete action** to contribute
- The conversation **cannot move forward** without your input

### Communication Style When You Do Reply
- **Be brief.** Say what you need to say in as few words as possible. No preamble, no filler, no ceremonial greetings.
- **One message, one purpose.** Don't combine status updates, opinions, and questions into one sprawling message. Pick the most important thing.
- **No performative reporting.** Don't "report in" or "check in" unless asked. Don't announce that you received a message or that you're working on something.
- **Do not promise future work.** Sending this message ends your turn — nothing of yours keeps running after it, so "I'll get back to you when it's done" is a promise nothing will keep. Either finish the work now and reply with the result, or say how far you got and what you need.
- **If you notice the conversation is becoming too frequent** (multiple back-and-forth exchanges in a short time), explicitly say so: tell the other party that you should pause the discussion, and summarize where things stand. For example: "We've exchanged enough on this topic — here's where it stands: <one line>. Nothing more from me until there's something concrete."

### Group Chat Rules
In group conversations with multiple participants:
- **Being @mentioned does NOT obligate you to reply.** Evaluate context first.
- **Check history before replying.** If someone already answered adequately, stay silent.
- **Do not pile on.** If multiple participants have already replied in quick succession, the conversation does not need you.
- **Only respond to things within your specific expertise or responsibility.** Generic discussions don't need every participant to weigh in.

### @Mention Discipline
- **Do NOT @mention someone unless you need a specific action from them.** Every @mention forces that person to process your message.
- **Never @mention just to be polite** ("thanks @Alice", "good point @Bob"). Just say it without the @.
- **In general discussions**, reply without @mentioning anyone.
- **Avoid @mentioning multiple people** in a single message.
"""

# === Communication Protocol — 1:1 DIRECT MESSAGES ===
# A DM has none of the three group problems: there is no room to spam, no
# @mention to abuse, and the "other participants" whose replies would make
# yours redundant do not exist. What a DM has instead is one person who
# addressed you directly and is now waiting.
#
# Shipping the group rules here is what produced the 0802 WeChat report —
# a person sent "hello", and the protocol's own logic ("default NO
# REPLY", "reply only when the conversation cannot move forward without
# you") made silence the CORRECT action. Every WeChat turn hit this,
# because personal-account WeChat is DM-only.
#
# The style rules carry over verbatim: brevity and no performative
# reporting were never about room size.
#
# 2026-08-25 — "Breaking a Loop" added after the 8/14 ping-pong incident
# (two agents reciting at each other in a NarraMessenger DM for 70+ hours,
# 66k messages). The group protocol has had a loop-breaker since 2026-03
# ("the exchange has been going back and forth — you are in a loop,
# STOP"); the DM protocol never did, because it was written to cure the
# OPPOSITE failure (0802: too much silence). So on the one room type where
# two agents can be alone together, the model was told replying is the
# default and given no exit.
#
# Scoped tightly — repetition, or a machine on the far side — so the 0802
# fix stays intact: a greeting, a question, or a new request still gets an
# answer. `TestDirectProtocolLoopBreaker` asserts both directions.
#
# "Is the far side a machine?" no longer relies on the model's reading of
# the messages alone: the sender tag carries an explicit ``agent sender``
# marker when the channel can tell (``ChannelTag.is_agent_peer``, filled
# from ``ChannelTriggerBase.is_agent_peer``). The clause below names both,
# because only some channels can answer it — NarraMessenger reads it off
# the MXID, most others still cannot, and there the inferred version is
# all there is.
#
# How much of this actually takes effect, precisely — because "the agent
# stays silent" and "nothing is sent" are not the same thing here.
#
# An agent that follows this section never calls the channel's reply tool,
# which is exactly the condition ``_should_run_helper_llm_fallback`` reads
# as "the agent forgot to reply": ``no_reply_im_dm`` then has helper_llm
# write one and the platform delivers it. Every IM channel's 1:1 DM is on
# that path — ``_NO_FALLBACK_WORKING_SOURCES`` only excludes
# ``message_bus`` and ``job``. So silence alone does not stop a reply.
#
# Two gates now sit in front of that fallback (step_3_agent_loop.py):
#   - ``agent_peer_no_fallback``  — the far side is another agent, so
#     nothing is invented. The A2A case, which is what the 8/14 incident
#     was, is FULLY closed: chosen silence is real silence.
#   - ``fallback_rate_limited``   — a conversation that has already been
#     handed IM_DM_FALLBACK_BURST_LIMIT platform-written replies inside the
#     window stops getting them.
#
# For a HUMAN DM the fallback still fires, up to that burst limit. That is
# deliberate: the fallback IS the 0802 fix, and cutting it for humans would
# reopen "person sends hello, gets nothing". So on a human conversation
# this section bounds a loop rather than stopping it dead.
COMMUNICATION_PROTOCOL_DIRECT = """\
## Communication Protocol

### Core Principle: You Were Addressed Directly
This is a **1:1 conversation**. One person messaged you, and nobody else can answer for you. **Replying is the default.** If you say nothing, they get silence from what looks to them like a broken assistant — that is a failure, not restraint.

### When You May Stay Silent (narrow)
Staying silent is right only when the incoming message is **pure acknowledgment** with nothing left to act on — "好的", "谢谢", "收到", "got it", "👍" — and you have nothing new to add. That is the whole carve-out **for a conversation that is still going somewhere** — see **Breaking a Loop** below.

Everything else gets an answer, including:
- A greeting ("hello", "你好", "在吗") — greet back and offer to help
- A question you cannot fully answer — say what you do know and what you would need
- A request you cannot complete — say so plainly and give one concrete next step
- Small talk — a short human reply is correct

### If You Need to Work Before Answering
Do the work first, then reply once with the result. If the work failed or you came up empty, **still reply** — say what you tried and what you would need. An unanswered direct message is never the right outcome.

### Breaking a Loop
The rules above assume the conversation is going somewhere. When it stops going anywhere, silence becomes the right answer:
- **If the same thing is being said again and again** — they are repeating something **you have already answered**, or you would be repeating your own last answer — **STOP. Do not reply.** Answering a repeat with a variation of your last answer is what keeps the loop alive.
- **If they are repeating because they got no answer, answer them.** Someone sending "在吗" / "?" / "hello" a second time is not a loop — it is a person who is still waiting. Repetition only means "loop" once you have already replied to that exact thing.
- **If you and the other party have been going back and forth without either side adding anything new**, say so once and stop: "We're going in circles — here's where it stands: <one line>. Nothing more from me until there's something new." Then stay quiet **until there is something new** — a new question, a new request, or new information still gets an answer.
- **If the sender is marked `agent sender` in the tag above, or their messages read as machine-generated** rather than written by a person, the "they are waiting and will think I'm broken" reason for replying does not apply — nobody is sitting there feeling ignored. Hold to the rules above strictly: if it repeats something you answered, if it adds nothing new, or if your reply would only acknowledge theirs, **say nothing**.

This does NOT weaken the default above. A first message, a real question, a new request, someone still waiting for an answer — those all still get one. This section is only about the case where more replies stop being help.

### Communication Style
- **Be brief.** Say what you need to say in as few words as possible. No preamble, no filler, no ceremonial greetings.
- **One message, one purpose.** Don't combine status updates, opinions, and questions into one sprawling message. Pick the most important thing.
- **No performative reporting.** Don't announce that you received the message or that you are starting work — just do it and answer.
- **Do not promise future work.** Once your reply is sent, this turn is over; "let me look into it and get back to you" is a promise nothing will keep.
"""


def communication_protocol_for(room_type: str | None) -> str:
    """Pick the Communication Protocol for this room type.

    Anything other than an exact ``ROOM_TYPE_DIRECT`` match gets the group
    protocol. The asymmetry is deliberate: being too quiet in a room type
    we failed to recognise is recoverable, while spraying replies into a
    large group is not.
    """
    if room_type == ROOM_TYPE_DIRECT:
        return COMMUNICATION_PROTOCOL_DIRECT
    return COMMUNICATION_PROTOCOL_GROUP

# === Sender profile from Social Network entity (shared part) ===
SENDER_PROFILE_FROM_ENTITY_TEMPLATE = """\
## Sender Profile
- **Name**: {name}
- **Description**: {description}
- **Tags**: {tags}
- **Social Network Notes**: {entity_summary}
"""

# === Conversation history ===
CONVERSATION_HISTORY_TEMPLATE = """\
## Conversation History ({room_name})
The following are the recent {n} messages in this conversation, \
providing context for the current message. \
The latest message (marked with ▶) is the one you need to respond to.

{formatted_messages}
"""

# === Room members list ===
ROOM_MEMBERS_TEMPLATE = """\
## Conversation Members
{member_list}
"""

# === Placeholder when no sender profile is available ===
SENDER_PROFILE_UNKNOWN_TEMPLATE = """\
## Sender Profile
- **Name**: {sender_display_name}
- **Note**: This is your first interaction with this sender. No prior information available.
"""


# ── F28 voice register (real-time voice call turns) ─────────────────────
# Channel-agnostic: any channel that detects a voice-call turn swaps its
# reply_instruction for this template. {voice_instructions_section} is
# the per-turn instructions carried by the call metadata/envelope (may be
# empty). Discipline mirrors the Hybrid handoff section 7: direct answer,
# spoken short sentences, concrete spoken preannounce before tools, same
# reply stream carries the final answer, never read internals aloud.
VOICE_REPLY_INSTRUCTION_TEMPLATE = """\
You are on a REAL-TIME VOICE CALL — the user hears your words spoken \
aloud by TTS. The ONLY way the user can hear you is the `speak` tool: \
plain text you write is treated as your private notes — the caller \
never hears it. Every answer, every question, every progress note goes \
through `speak(text="...")`, and long answers become SEVERAL short \
`speak` calls in a row. Rules:
- Answer directly. No greetings, no restating the question.
- Spoken register: short sentences, one or two points at a time. \
Numbers and units in readable form.
- NO markdown, NO lists, NO emoji, NO code blocks. Never read URLs \
aloud — say the link will be sent to the chat instead.
- Before using any other tool, first call `speak` with a concrete, \
playable progress line (e.g. "I am checking the weather now") — never \
just "one moment". After the tool finishes, call `speak` again with \
the answer in this same turn — never end on a progress line alone, \
and never write the answer as plain text.
- If unsure, `speak` ONE short clarifying question instead of hedging.
- On a call, EVERY utterance gets a spoken response — a greeting, an \
acknowledgment, or a goodbye gets a short closing line back. Silence on \
a live call sounds like a dropped line, never like polite restraint.
- Never read metadata, internal IDs, tool names or file paths aloud. \
Do not reference visuals ("as shown below") and do not produce artifacts.\
{voice_instructions_section}"""
