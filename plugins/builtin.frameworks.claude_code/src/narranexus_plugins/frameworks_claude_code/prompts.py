"""
@file_name: prompts.py
@author: NetMind.AI
@date: 2025-11-15
@description: Prompt definitions for Agent Framework (Claude Agent SDK)
"""

from collections.abc import Sequence

# ============================================================================
# Chat History Header
# Separator added when building the system prompt in agent_loop() for history records
# ============================================================================
CHAT_HISTORY_HEADER = "\n\n=== Chat History ===\n"

# ============================================================================
# Truncated Chat History Header
# Separator used in agent_loop() when the history is too long and gets truncated
# ============================================================================
CHAT_HISTORY_TRUNCATED_HEADER = "\n\n=== Chat History (truncated) ===\n"

# ============================================================================
# Chat History End Instruction
# Instruction text appended after the chat history in agent_loop()
# ============================================================================
CHAT_HISTORY_END_INSTRUCTION = "\n=== Chat History End ===\n These are the chat history between you and the user. This time please make the response by user input in this turn."

# ============================================================================
# System Prompt Truncation Warning
# Truncation notice appended when the system prompt exceeds the length limit in agent_loop()
# ============================================================================
SYSTEM_PROMPT_TRUNCATION_WARNING = "\n\n[...truncated due to length limit...]"

# ============================================================================
# Task-list tools notice (platform-declared, per run)
#
# The Claude Code CLI ships a task LIST feature (TaskCreate / TaskGet /
# TaskList / TaskUpdate) whose store lives only inside the CLI process; the
# platform never reads it. Under the SDK's headless spawn the CLI already
# leaves that family off (sdk.TASK_LIST_TOOLS has the gate), so the tools are
# "not available" here rather than "taken away". What the model DOES hold is
# its TodoWrite checklist — equally run-scoped, deliberately kept — and this
# notice tells the model where work that must outlive the run goes. It says
# nothing against in-run background commands: ``Bash(run_in_background)``
# with TaskOutput / TaskStop keeps working and is read by the model itself.
# The rule is generic — it names the platform's Job module, never a scenario —
# and the tool list is rendered from sdk.TASK_LIST_TOOLS so the notice and the
# pinned-off set cannot drift apart.
# ============================================================================
TASK_LIST_TOOLS_NOTICE_TEMPLATE = (
    "\n\n---\n"
    "Task list: the coding agent's built-in task-list tools ({tools}) are "
    "not available on this platform, and your TodoWrite checklist lives only "
    "inside this run — nothing reads either back later. Background commands "
    "within this run (run_in_background, then TaskOutput / TaskStop) still "
    "work as usual. For work that must outlive this run — deferred, "
    "scheduled, or recurring — use the platform's Job module tools "
    "(job_create and friends) when they are available to you; otherwise "
    "finish the work in this turn."
)


def task_list_tools_notice(tools: "Sequence[str]") -> str:
    """Render the task-list notice for the CLI tools ``sdk.py`` pins off.

    Appended once to the base system prompt (stable bytes across turns, so the
    cache prefix is unaffected). Empty ``tools`` → empty string: nothing
    disabled means nothing to explain.
    """
    names = tuple(tools or ())
    if not names:
        return ""
    return TASK_LIST_TOOLS_NOTICE_TEMPLATE.format(tools=", ".join(names))

# ============================================================================
# Reply-surface reminder (platform-declared, per turn)
#
# The CLI has no per-step injection seam, so the closest-to-generation
# position we control is the end of the turn's user message. The tool list
# is TurnInput.expressive_tools — the same declaration NexusPower's
# per-step reply reminder consumes — rendered, never hard-coded, so both
# frameworks speak from one source of truth. The general rule stays fixed;
# only the data (which tools deliver on THIS turn's origin) varies.
# ============================================================================
REPLY_REMINDER_TEMPLATE = (
    "\n\n---\n"
    "Reminder: whoever contacted you this turn receives ONLY what you send "
    "through a reply tool ({tools}; the first is this turn's default, "
    "matching where the contact came from). A plain-text answer is never "
    "delivered to them. Reporting to your owner does not replace replying "
    "to the contact. If the message above names a specific channel or tool "
    "to answer through, that instruction outranks this list."
)


def append_reply_reminder(
    user_message: str,
    expressive_tools: "Sequence[str] | None",
    origin_declaration: str = "",
) -> str:
    """Append the origin declaration + reply-surface reminder to the turn's
    user message.

    No declaration → message untouched. That covers the one case left:
    "unknown reply surface", where inventing a tool name is worse than
    silence. Team rooms USED to land here too — their surface was
    deliberately empty because plain text auto-posted — and that exception
    is gone: a team reply is a tool call like every other, so the rule
    "plain text reaches nobody" now holds with no carve-out anywhere.

    ``origin_declaration`` is pre-rendered by the step layer
    (``render_origin_declaration``) so this function composes, never phrases.
    """
    tools = tuple(expressive_tools or ())
    if not tools:
        return user_message
    prefix = f"\n\n{origin_declaration}" if origin_declaration else ""
    return (
        user_message
        + prefix
        + REPLY_REMINDER_TEMPLATE.format(tools=", ".join(tools))
    )
