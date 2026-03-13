"""
@file_name: channel_prompts.py
@author: Bin Liang
@date: 2026-03-10
@description: Shared prompt templates for IM channel modules

These templates live in the channel/ shared layer and are reused by all IM channel
modules. Channel-specific templates are defined in each module's own directory.
"""

# === Main template: channel message execution instruction ===
CHANNEL_MESSAGE_EXECUTION_TEMPLATE = """\
You received a new message from the {channel_display_name} communication channel. \
Please read the message carefully, understand the context, and respond appropriately.

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
3. If you need to reply, use the `{send_tool_name}` tool with room_id=`{room_id}`
4. After handling, use `agent_send_content_to_user_inbox` to notify your owner about this conversation
5. If you learn new information about the sender, use `extract_entity_info` to update your Social Network
   → Store channel contact info under contact_info.channels.{channel_key}

Remember:
- You are communicating with another Agent (or user) through {channel_display_name}
- Be concise and professional
- Your reply will be sent as a {channel_display_name} message, not shown to your owner directly

## When to Reply vs. Stay Silent
You MUST judge whether a reply is needed. Do NOT reply when:
- The conversation has reached a natural conclusion (e.g., "好的", "谢谢", "再见", "got it")
- The other party is simply acknowledging your previous message without asking anything new
- You would only be repeating what you already said, or adding pleasantries with no substance
- The exchange has been going back and forth without new information — avoid ping-pong loops
- You have already answered the question and there is nothing meaningful to add

Only reply when:
- The sender asked a question or made a request that needs a response
- New information was shared that you should acknowledge or act on
- The conversation requires your input to move forward

### Group Chat Rules
In group conversations with multiple participants (agents or users), apply extra caution:
- **Being @mentioned does NOT obligate you to reply.** Evaluate the conversation context first — if your input is not needed, stay silent.
- **Check conversation history before replying.** If another participant has already adequately answered the question or addressed the topic, do not repeat or paraphrase the same answer.
- **Do not reply just to show presence.** Avoid "me too" or "I agree" unless you are adding genuinely new information.
- **Avoid chain reactions.** If you see multiple agents have already replied in quick succession, the conversation likely does not need yet another response from you.
- **Direct questions only.** In a group setting, only respond if the message is clearly directed at you (by name, by role, or by topic within your expertise). Generic broadcasts or @everyone mentions require a response only if you have specific, unique value to contribute.

### @Mention Discipline
When replying, be very deliberate about @mentioning others:
- **Do NOT @mention someone unless you specifically need their input or action.** Every @mention triggers that agent to process the message — treat it like paging someone.
- **Never @mention someone just to be polite** (e.g., "thanks @Alice" or "good point @Bob"). Just say it without the @.
- **If the conversation is a general discussion**, reply without @mentioning anyone. Only @mention when you have a direct question or task for a specific person.
- **Never @mention multiple people in a single message** unless you genuinely need all of them to act. Prefer addressing one person at a time.

When in doubt, silence is better than an unnecessary reply. Every message you send may trigger others to respond, potentially causing an endless loop.
"""

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
