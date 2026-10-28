"""
@file_name: message_bus_module.py
@author: NarraNexus
@date: 2026-04-02
@description: MessageBusModule - Agent-to-agent communication via MessageBus

Protocol-agnostic message bus for agent-to-agent communication. Provides MCP
tools for sending/receiving messages, managing channels, and discovering agents.

Instance level: Agent-level (one per Agent, is_public=True).

Behavior design:
- Reply Discipline: prevent infinite trigger loops between agents
- Selective mark_read: a DM the agent ignores stays unread and resurfaces next
  turn. NOT true of a team room, which delivers by rendering its scrollback and
  advances the read cursor once a turn has rendered the window, answered or not
  (a backlog reaching below that window holds the cursor — see `_ack_room_seen`)
- Context caps: unread/channels/known_agents all bounded to prevent pollution
- Source recognition: entries in the unread list are tagged
  [from sender] / ["Team X" · from sender] (see `_bus_tag`). This is the ONLY place the
  tag appears — a branch that prefixed the turn's INPUT with it was deleted on
  2026-08-17 after it turned out never to have executed; do not describe an
  input prefix here again without checking that one exists
"""

from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from narranexus.platform.module_system.base import (
    XYZBaseModule,
    mcp_server_url,
    working_source_matches,
)
from narranexus.platform.schema.module_schema import ModuleAgentInstance, ModuleDisplay
from narranexus.platform.schema import (
    BUS_TEAM_ROOM_EXTRA_KEY,
    is_plain_text_turn,
    ModuleConfig,
    MCPServerConfig,
    ContextData,
    HookAfterExecutionParams,
    WorkingSource,
    is_agent_description_unset,
)
from narranexus.platform.utils.inline_field import (
    INLINE_DESCRIPTION_MAX_CHARS,
    body_lines,
    inline_field,
)
from narranexus.platform.message_bus.system_messages import (
    PLATFORM_MSG_TYPES,
    SYSTEM_SENDER_LABEL,
)
from narranexus.platform.schema.team_schema import (
    TEAM_ROOM_OWNER_PREFIX,
    USER_SENDER_PREFIX,
)
from narranexus.platform.settings import settings


# MCP server port for MessageBus tools
# Context-injection caps to prevent pollution
MAX_UNREAD_IN_CONTEXT = 20
MAX_KNOWN_AGENTS_IN_CONTEXT = 50
# The standing "your teams" address book. An agent's team count is small (a
# team is a deliberately-created room, not an auto-grouping), so this cap is a
# guard against a pathological owner, not an expected trim.
MAX_TEAMS_IN_CONTEXT = 30
#: Per-row character budget of the unread list. It used to be 200 and cut
#: silently, which is how a three-paragraph team-room instruction reached an
#: agent as its first 200 characters and was acted on as if complete (upstream
#: issue #73). The budget now fits an ordinary multi-paragraph instruction, and
#: anything past it is ANNOUNCED — see `_unread_row`. Counted in message
#: characters, so the marker's "only the first N are shown" is literally true.
UNREAD_PREVIEW_MAX_CHARS = 1000
#: Budget for the WHOLE unread list, counted on the rendered rows (tag, part
#: label, body indentation and cut marker included) plus the line announcing
#: what is not shown. Unread direct messages
#: resurface every turn until answered, so without a span budget a handful of
#: long ones would push ~20 rows x ~1000 characters into every turn. Rows are
#: admitted newest first; the newest row is always shown, and whatever does
#: not fit is announced by count with the calls that fetch it — never dropped
#: silently.
UNREAD_SPAN_MAX_CHARS = 8000
#: Opens the line that says a row was cut. It is emitted on a line of its own
#: at two-space indent, a position no message body can occupy (see
#: `_unread_row`), so a body that types this text cannot pass for the marker.
UNREAD_CUT_MARKER = "[cut:"
#: Upper bound on the read_history calls one not-shown line lists. Without it
#: the line grows with every distinct sender it gives back, and giving back a
#: short row could make the span LONGER (see `_not_shown_line`).
NOT_SHOWN_MAX_CALLS = 3


def _read_rest_call(from_agent: Any, msg_type: Any, team_id: str) -> str:
    """The exact `read_history` call that returns this row's conversation, or
    "" when the agent holds no handle for it.

    `read_history` takes `team_id` (a team room) or `with_agent` (a private
    conversation with an agent). A team-room row carries its `team_id` when the
    room resolved; a private row from a real agent uses that agent's id — the
    same raw id its tag shows. A `usr_*` or platform sender outside a resolved
    room has no handle the tool accepts, and pretending otherwise would train
    the agent to ignore the pointer.
    """
    if team_id:
        return f'read_history(team_id="{team_id}")'
    sender = _render_sender(from_agent, msg_type)
    if sender and sender == str(from_agent or "") and sender != "unknown":
        return f'read_history(with_agent="{sender}")'
    return ""


def _unread_row(tag: str, content: Any, part_label: str, read_call: str) -> str:
    """One unread row: whole when it fits, otherwise cut AND marked.

    The marker states both sizes and the exact call that returns the full text,
    because the static rules tell the agent its unread messages are already in
    context. A clipped row that reads as complete is a wrong instruction the
    agent has no way to notice.
    """
    text = str(content or "")
    cut = len(text) > UNREAD_PREVIEW_MAX_CHARS
    shown = text[:UNREAD_PREVIEW_MAX_CHARS] if cut else text
    row = f"- `{tag}` {part_label}{body_lines(shown)}"
    if not cut:
        return row
    how = (
        f"read the rest with {read_call}"
        if read_call
        else "the rest cannot be fetched from here; ask the sender for it"
    )
    return (
        f"{row}\n  {UNREAD_CUT_MARKER} this message is {len(text)} characters "
        f"and only the first {UNREAD_PREVIEW_MAX_CHARS} are shown. Before "
        f"acting on it, {how}.]"
    )


def _not_shown_line(total: int, kept: int, rows: list[tuple[str, str]]) -> str:
    """The line announcing every unread message the list does not print, or ""
    when it prints them all.

    ``rows`` is the rendered window as ``(row, read_call)`` in reading order
    (newest last) and ``kept`` how many of its newest rows are shown. The count
    is ``total - kept`` — rows the span budget gave back AND messages beyond the
    query window — the same subtraction as the header's ``showing``. The calls
    listed are honest about their reach: they come only from the given-back
    rows, at most ``NOT_SHOWN_MAX_CALLS`` of them (newest first) with the rest
    counted, and messages beyond the window are named as such, since no row
    here carries a handle for them.
    """
    missing = total - kept
    if missing <= 0:
        return ""
    omitted = rows[: len(rows) - kept]
    beyond = missing - len(omitted)
    line = f"- {missing} unread message(s) not shown (this list shows the newest {kept})"
    if omitted:
        calls = list(dict.fromkeys(c for _, c in reversed(omitted) if c))
        listed = calls[:NOT_SHOWN_MAX_CALLS]
        line += f"; the {len(omitted)} just older than the list"
        if listed:
            line += " can be read with " + ", ".join(listed)
            if len(calls) > len(listed):
                line += f" and {len(calls) - len(listed)} more conversation(s)"
            if any(not c for _, c in omitted):
                line += " (some come from senders with no read_history handle)"
        else:
            line += " come from senders with no read_history handle"
    if beyond > 0:
        # With no given-back rows there is no "those" to be older than: the
        # messages are simply older than the whole list.
        where = (
            f"the {beyond} older than those are beyond this list"
            if omitted
            else f"all {beyond} are older than this list"
        )
        line += (
            f"; {where} — use "
            "read_history on the conversation you expect them in"
        )
    return line + "."


def _render_sender(from_agent: Any, msg_type: Any = None) -> str:
    """How a sender is named inside a `[from … ]` tag.

    THREE kinds of sender reach this list, and only one of them is an agent.
    `schema/team_schema` defines both synthetic prefixes side by side, each
    marked "a non-agent sender":

    * ``usr_<user_id>`` — a PERSON. A team room carries its owner's own
      messages, and the raw id says nothing at all, least of all that the
      reader is looking at the person it works for.
    * ``team_<team_id>`` — the ROOM itself, i.e. the platform. Printing it
      verbatim invents a teammate the agent may then try to @mention back,
      which is exactly why `message_bus_trigger._who` refuses to.
    * anything else — a real agent.

    ``msg_type`` outranks the sender, and has to: a bulletin notice posted
    from the UI carries `msg_type=system_bulletin` but is stamped with the
    OWNER's ``usr_`` id (`team_bulletin.py`, no actor), so on the sender alone
    it reads as the owner speaking — and the Source-Recognition rules now say
    in as many words that a `User` sender is a person. Checking the type first
    is what stops the platform being quoted as the human.

    Named to match `message_bus_trigger._sender`, which renders the same rows
    in the room's own prompt — but the agreement covers only the two synthetic
    kinds: `usr_*` is "User" on both sides and a platform row is
    ``SYSTEM_SENDER_LABEL`` on both. An ORDINARY agent deliberately differs:
    the room prompt resolves it through `member_map` to a display name, while
    this list keeps the raw ``agent_id`` because that is what `message_agent`
    takes. Worth knowing, since a team turn shows both — "Alice: …" in the
    scrollback and `agent_x7f3…` in the list, for one peer.
    """
    if (str(msg_type or "")) in PLATFORM_MSG_TYPES:
        return SYSTEM_SENDER_LABEL
    sender = str(from_agent or "unknown")
    if sender.startswith(TEAM_ROOM_OWNER_PREFIX):
        return SYSTEM_SENDER_LABEL
    if sender.startswith(USER_SENDER_PREFIX):
        return "User"
    return sender


def _bus_tag(from_agent: Any, where: Any = "", msg_type: Any = None) -> str:
    """The `[from sender]` / `["Team X" · from sender]` marker, built in one place.

    Renamed off "MessageBus" on 2026-08-17. The word named a subsystem the agent
    is no longer supposed to know exists — it thinks in "a private message" and
    "a team room", and a tag announcing the transport underneath is a third
    concept doing no work. The acceptance criterion for the redesign is that
    `MessageBus` appears in NO agent-visible text.

    The format had three copies — the worked example in the instructions and
    two render sites — and the example disagreed with both, showing a display
    name AND an id where the code emits a single sender field. That was
    survivable while the tag was decoration. It stopped being survivable when
    the rules started telling the agent to READ the sender out of it: an agent
    following a four-field example against a three-field tag looks for a name
    that is never there, and the field it finds in position two is the channel.

    So the example is generated by this same function, and the two can no
    longer drift apart. ``msg_type`` is optional precisely so the example stays
    constructible from two literals.

    ``where`` was the raw `channel_id` until 2026-08-18, which put a channel id
    on EVERY message row — a harder contradiction of "an agent has private
    conversations and teams, not channels" than the channel list removed the
    same day, because it was unavoidable rather than merely present. It is now
    the team's own name, and only for a team room: in a private conversation
    the sender IS the conversation, so a second field would be the same fact
    twice. Empty renders the short form.
    """
    sender = inline_field(_render_sender(from_agent, msg_type), None)
    label = inline_field(where) if str(where or "").strip() else ""
    return f"[{label} · from {sender}]" if label else f"[from {sender}]"


class MessageBusModule(XYZBaseModule):
    """
    MessageBus communication module.

    Enables Agents to communicate with each other via the MessageBus service.
    Provides MCP tools for messaging, channel management, and agent discovery.

    Instance level: Agent-level (one per Agent, is_public=True).
    """

    # =========================================================================
    # Configuration
    # =========================================================================

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="MessageBusModule",
            discovery_hidden=True,
            agent_instance=ModuleAgentInstance(description="Agent-to-agent communication via message bus", keywords=["message_bus", "communication", "messaging", "agent"], topic_hint="Inter-agent messaging via message bus"),
            default=True,
            instance_prefix="bus",
            display=ModuleDisplay(icon="📡", name="MessageBus", desc="Inter-agent messaging"),
            priority=5,
            enabled=True,
            description=(
                "Agent-to-agent communication via message bus. "
                "Provides tools for sending/receiving messages, managing channels, "
                "and discovering other agents."
            ),
            module_type="capability",
        )

    # =========================================================================
    # MCP Server
    # =========================================================================

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="message_bus_module",
            server_url=mcp_server_url("message_bus_module"),
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        try:
            # Use the official mcp SDK's FastMCP (same as every other module)
            # so FASTMCP_HOST, TransportSecuritySettings, and the shared
            # module_runner._run_mcp_in_thread configuration all apply
            # uniformly. The standalone `fastmcp` v2 package has a different
            # API and does not honour those settings, which caused this MCP
            # to silently fail cross-container reachability.
            from mcp.server.fastmcp import FastMCP

            mcp = FastMCP("message_bus_module")

            from ._message_bus_mcp_tools import register_message_bus_mcp_tools
            register_message_bus_mcp_tools(mcp, get_message_bus_fn=_get_default_bus_async)
            # The team work board rides the same MCP server: its items are
            # scoped to a team ROOM, which is a bus channel, so an agent that
            # can talk in the room is exactly the agent that can maintain its
            # board. Separate file, separate state machine — see
            # _work_board_mcp_tools for the platform/model write boundary.
            from ._work_board_mcp_tools import register_work_board_mcp_tools
            register_work_board_mcp_tools(mcp)

            logger.info("MessageBusModule MCP server created")
            return mcp
        except Exception as e:
            logger.exception(f"Failed to create MessageBusModule MCP server: {e}")
            return None

    # =========================================================================
    # Reply surface (origin-aware declaration)
    # =========================================================================

    def claims_source(self, working_source: Any) -> bool:
        """This module is the origin of MESSAGE_BUS turns — the collection
        sorts the origin module's declaration first, so the bus delivery
        tool becomes the turn's default reply tool."""
        return working_source_matches(working_source, WorkingSource.MESSAGE_BUS.value)

    def _is_team_turn(self, ctx_data: Any = None) -> bool:
        """Is this turn a team room?

        Reads the marker MessageBusTrigger stamps into `trigger_extra_data`
        (`bus_team_room`), which reaches `ctx_data.extra_data`.
        """
        extra = getattr(ctx_data, "extra_data", None) or {}
        return bool(extra.get(BUS_TEAM_ROOM_EXTRA_KEY))

    @staticmethod
    def _is_plain_text_turn(ctx_data: Any = None) -> bool:
        """Does this turn deliver by SPEAKING rather than by calling a tool?

        Only patrol. Kept separate from `_is_team_turn` because a patrol turn is
        also a team-room turn — it carries both markers — and the room marker on
        its own would declare `message_team`, which the patrol prompt forbids.

        Delegates to the shared `is_plain_text_turn` (schema.hook_schema) so the
        predicate has one definition across every module that gates on it.
        """
        return is_plain_text_turn(ctx_data)

    async def expressive_tools(self, ctx_data: Any = None) -> list[str]:
        """The ONE verb the reply reminder defaults to this turn.

        Declared only on a bus turn — advertising it on an owner-chat turn
        invites replying to the owner over the bus (2026-08-04). This names the
        DEFAULT reply target (`message_team` in a room, `message_agent` in a
        DM), so the path of least resistance is "answer where you were spoken
        to". It does NOT bound what the agent can reach: the other send verbs
        stay on the desk (`disallowed_tools` no longer removes them), the
        reminder just does not name them. Reaching another conversation is a
        deliberate act, one `tool_search` away.
        """
        if not self.claims_source(getattr(ctx_data, "working_source", None)):
            return []
        if self._is_plain_text_turn(ctx_data):
            # Nothing to declare: the turn's reply IS its plain text. Declaring
            # `message_team` here is what made both frameworks' reply reminders
            # name the one tool the patrol prompt forbids — and on NexusPower
            # the mute-turn nudge then told a correctly-silent lead to call it.
            return []
        config = await self.mcp_server()
        name = "message_team" if self._is_team_turn(ctx_data) else "message_agent"
        return [f"mcp__{config.server_name}__{name}"]

    async def disallowed_tools(self, ctx_data: Any = None) -> list[str]:
        """Take off the desk only the verbs no turn of this KIND can deliver
        through — never the ones the trigger channel simply is not the default
        for.

        Capability follows the agent, not the trigger channel. The declaration
        above decides which verb the reply REMINDER defaults to; it does NOT
        decide what the agent can reach. An agent woken in a DM that is asked to
        post in a team room it belongs to must find `message_team` on its desk
        (or one `tool_search` away) — removing it because "this turn came in
        through a DM" is the exact defect that left a whole squad unable to
        follow "post your receipts in the room, not in my DMs". So the
        trigger-channel drop is gone: both internal send verbs stay reachable on
        every ordinary bus turn.

        The one turn that still clears both is patrol: it delivers by SPEAKING —
        the platform posts its composed line and it calls no send tool — so a
        send schema on that desk is a tool the patrol prompt forbids. Leaving it
        there is precisely how a prose prohibition loses, which is why the
        suppression, not the prose, carries it.

        Reads the turn from its own ``ctx_data``, not from state the
        declaration left behind: the runtime calls THIS hook first.
        """
        config = await self.mcp_server()
        if self._is_plain_text_turn(ctx_data):
            return [
                f"mcp__{config.server_name}__message_agent",
                f"mcp__{config.server_name}__message_team",
            ]
        return []

    def _static_instruction_parts(self) -> list:
        """The usage-rules half of the instruction — constant for a given agent,
        so it can live in the byte-stable system prompt (R4 relocation).

        BYTE-STABLE AND SURFACE-BLIND: this block is emitted on every
        bus-enabled turn — owner chat, job, each IM channel, peer DM, team room —
        one character identical. Two rules follow and neither is optional:

        1. **Every sentence here must be true on every one of those surfaces.**
           A claim that holds in only one kind of room creates a contradiction
           inside one context window, because that room's own turn prompt is a
           few dozen lines away saying the opposite. Six review rounds on PR
           #311 went to exactly this.
        2. **Never branch on room type.** Branching destroys the byte-stability
           this block exists for. Say what holds everywhere and leave the
           room-specific fact to the room's own prompt — the only place that
           knows it.

        Corollaries, each one paid for: do not infer where a turn came from
        from the ABSENCE of a marker (wrong twice); do not describe a mechanism
        without checking it exists (a whole section once pointed at a branch
        that had never run); when you change wording here, sweep the other
        copies — the file header, the method docstrings, the MCP tool
        docstrings, and the mirror md.
        """
        return [
            "## Talking to people and to other agents",
            "",
            # Scoped rather than absolute, because this block is byte-stable
            # (R4 prefix caching) and reaches EVERY turn — including owner-chat
            # and job turns, which are neither of these two situations and carry
            # no such opening line. "the top of each turn tells you" was simply
            # false there, which is the defect this whole redesign is about, in
            # the text that introduces it.
            "You are not alone. Two kinds of conversation can reach you. When "
            "one of them is what woke you, the top of the turn says which and "
            "names the one call that answers it.",
            "",
            f"Your agent ID: `{self.agent_id}`",
            "",
            "### Private messages with another agent",
            "",
            "- `message_agent(to=<agent id>, text=...)` — write to one peer. The "
            "same call whether you are answering someone who just wrote to you "
            "or starting something yourself; messaging a peer is one act.",
            "- `to` is required. A turn can involve several peers, so the "
            "platform does not guess which one you mean. Who wrote to you is "
            "named at the top of the turn; everyone else you can reach is in "
            "your Known Agents list.",
            "- `find_agent(query)` finds peers by what they can do, for when the "
            "one you need is not in that list yet.",
            "- Writing to an agent starts a full turn for them, and their answer "
            "arrives as a NEW turn — not inside this one. So write when you have "
            "something for them, and do not wait around for a reply.",
            "",
            "### Team rooms",
            "",
            "A team is a shared room: you, your teammates, and your owner all see "
            "everything in it.",
            "",
            "- `message_team(team_id=..., text=...)` — say something in a room.",
            # Capability follows the agent, not the trigger channel: the trigger
            # channel decides only where a plain reply goes by default, never
            # what the agent can reach. Byte-stable and surface-blind (R4): the
            # "unless this turn's own prompt says otherwise" clause is what keeps
            # the sentence TRUE on the one surface that does take the send verbs
            # off the desk — patrol — without this block branching on room type
            # (which its own docstring forbids). Do not drop that clause.
            "- The conversation that woke you is only where a plain reply goes "
            "by default (the top of the turn names it) — you are not confined "
            "to it. Unless this turn's own prompt says otherwise, you can reach "
            "any team or peer in your context: `message_team` for a room you "
            "belong to, `message_agent` for a peer. Reaching a conversation "
            "other than the one that woke you is a deliberate act — name the "
            "target.",
            "- `team_id` is required: you can belong to several teams, so the "
            "platform does not pick one for you. The room a turn is about is "
            "named at the top of it, and every team you belong to is listed with "
            "its id below.",
            "- `create_team(name, members)` — start a new team when work needs "
            "more than two people. It becomes a real room your owner can see, "
            "not a private side-channel.",
            "- `team_share_file` puts one of your files in the team's shared "
            "folder; `team_list_files` says what is already there. Both beat "
            "describing a file nobody can open.",
            "- `team_pin_rule` fixes a convention that should govern FUTURE "
            "replies (an output format, where files go) so nobody has to repeat "
            "it. Findings and status belong in the conversation, not in the rules.",
            "- The board — `team_work_add`, `team_work_list`, `team_work_claim`, "
            "`team_work_complete`, `team_work_update_status` — is how work "
            "outlives a turn. A task that exists only in a message is a task "
            "nobody can notice has stalled, including you, because this turn's "
            "memory is gone by the next one.",
            "",
            "### @mentions decide who WAKES UP, not who can see",
            "",
            "In a room, only @-mentioned agents are activated: @mentioning a "
            "teammate starts a full turn for them, and a message that names "
            "nobody wakes nobody. That is how work is handed on, so do it "
            "deliberately and not as a reflex. Say @all only when you genuinely "
            "need everyone.",
            "",
            "What you can READ in a given room is a property of that room, and "
            "its own prompt states it. This block only speaks to activation, "
            "which works the same way everywhere.",
            "",
            "When agents have passed something back and forth several times with "
            "no human word in between, the platform stops relaying @mentions and "
            "says so in the room. That is a fact about the room, not a rule for "
            "you to enforce: you will be told who was not reached.",
            "",
            "### Reading what you were given",
            "",
            "Each entry in your unread list is tagged with who sent it and where, "
            f"e.g.: `{_bus_tag('agent_xxx')}` in a private conversation, "
            f"`{_bus_tag('agent_xxx', 'Ops')}` in a team's room.",
            "- A sender shown as `User` is a **PERSON**, not an agent. A person "
            "can be in these conversations: a team room carries its owner's own "
            "messages. So read the sender rather than assuming a machine.",
            f"- A sender shown as `{SYSTEM_SENDER_LABEL}` is the PLATFORM "
            "narrating something it did — not a teammate, and not someone to "
            "answer or @mention.",
            "- These tags describe your unread QUEUE. They say nothing about what "
            "started THIS turn — that is stated at the top of the turn, so do not "
            "infer it from the presence or absence of a tag here.",
            "",
            "### Looking things up",
            "",
            "- Your unread messages and the current conversation are already in "
            "this turn's context. You do not need to fetch them — except where "
            "the unread list says otherwise: it states when a message is shown "
            "cut and when older messages are not shown, and read_history "
            "returns them in full.",
            "- `read_history` is for going back FURTHER than what you were given "
            "— when the answer depends on something older than this turn shows.",
            "- There is no registration tool. What peers see of you is rebuilt "
            "from your profile and your installed skills every turn; change it "
            "with `update_agent_profile` (Awareness). Capabilities are derived, "
            "never self-declared.",
            "",
            "### Autonomy — be proactive",
            "",
            "You are expected to handle what reaches you **autonomously**, not as "
            "a passive intermediary.",
            "- Another agent asks something you can answer → answer it. Do NOT "
            "ask your owner for permission first.",
            "- Another agent asks for help you can give → do it and say what "
            "came of it.",
            "- Treat these as real work, exactly like a request from your owner.",
            "- Escalate to your owner only when the request is outside what you "
            "can do, needs their approval, or involves a sensitive decision.",
            "",
            "### Reply Discipline — CRITICAL (prevents infinite loops)",
            "",
            "Autonomy does not mean answering everything. Every message you send "
            "starts a turn for someone, which can cascade. So:",
            # The species claim is CONDITIONAL. A team room carries its owner's
            # own messages, so a flat "the other party is a machine" aims the
            # skip-the-pleasantries rule at a person.
            "- **When the other party is another agent, it is not a human.** Skip "
            "pleasantries and warm-up phrases. Brevity beats politeness — one "
            "sentence beats three, and a single number or status word beats a "
            "sentence wrapped around it. Agents do not need to feel acknowledged. "
            "When the sender is a PERSON, talk to them like one.",
            "- **Silence when the thread is done.** If the other party only "
            "acknowledged ('thanks', 'got it', '好的'), do not answer again. The "
            "conversation reached its end.",
            "- **Do NOT ping-pong.** Once you have answered and they have "
            "acknowledged, stop. Another message adds nothing and starts another "
            "turn.",
            # 2026-08-03: 小雀 relayed its owner's question, 羽书 classified it as
            # "just forwarding" and stayed silent — so the human who asked never
            # got an answer. Silence is for ACKNOWLEDGEMENTS, never for questions.
            "- **A question is never ping-pong — answer it.** Including one "
            "somebody is relaying on their owner's behalf ('X wants to know what "
            "you are working on'): that a message is 'just forwarded' is NOT a "
            "reason for silence, because a human is waiting at the other end of "
            "it. And "
            "reporting the same thing to YOUR owner does not discharge the request "
            "— the agent who asked cannot see what you told your owner.",
            # 2026-08-01 briefing squad: five analysts did real research and ended
            # their turns with the results as plain text. Nothing was delivered.
            "- **Finished work is never ping-pong — deliver it.** When you "
            "complete something someone asked for (research, an answer, a "
            "document), it has to REACH them — plain text sitting in your own "
            "reasoning reaches nobody. Send it through the call for the "
            "conversation it belongs to; the top of the turn names the default "
            "one. (No tool name is given here on purpose: which call fits "
            "depends on the conversation, and only the turn knows.)",
            "- **Do NOT repeat yourself — or anyone else.** If you have already "
            "said X, do not rephrase X to fill space; if a teammate already said "
            "or accepted X, do not restate it back to them, and do not post a "
            "summary of what the conversation already contains.",
            "- **Substance only.** Write when you have new information, a "
            "concrete answer, a real question, or a result. Not 'I'm thinking "
            "about it', 'got your message', 'will get back to you' — and not "
            "a reply that only agrees.",
            "- **If there is no substance, choose silence explicitly** — make no "
            "send call to that conversation at all. Silence is producing NOTHING, "
            "not producing something short. It is silence toward the PEER OR ROOM "
            "only: reporting to your owner is a separate act and this never "
            "suppresses it.",
            "- **An unanswered private message comes back.** A peer's message you "
            "choose not to answer stays unread and appears again next turn, so "
            "you can defer without forgetting. (A room works differently, and its "
            "own prompt says how.)",
            "",
            "### When your owner asks about your messages",
            "",
            "If they ask 'what messages do you have' or 'check your inbox', "
            "report what is there. That is a status question, not permission to "
            "start answering peers.",
            "",
            # P1 2026-08-02: agents answered "I can't do that" to "ask X what
            # they're working on". The capability was always there — the model
            # reached for a contact-lookup tool, hit an error, and gave up.
            "### When your owner asks you to find something out FROM another agent",
            "",
            "'Ask the teaching expert what they're working on', 'check whether X "
            "finished', 'find out if Y needs help' — these are things you CAN do. "
            "**Never answer that you are unable to reach another agent.**",
            "",
            "1. Find them in your Known Agents list (or with `find_agent` if they "
            "are not there yet) and use that exact id.",
            "2. Ask: `message_agent(to=<their id>, text=<your owner's question>)`. "
            "Do NOT use social-network or contact-lookup tools for this — those "
            "return contact details, not answers.",
            "3. Tell your owner you have asked and will report back. Their reply "
            "is a NEW turn, not something you wait for inside this one.",
            "4. When it arrives, relay the substance to your owner with "
            "`notify_owner`. That relay is the point of the "
            "errand — do not drop it. Reply discipline governs what you send to "
            "the AGENT; it never suppresses reporting back to your owner.",
            "",
            "If you genuinely cannot find them, say who you looked for and ask "
            "your owner which agent they meant — that is a clarifying question, "
            "not a refusal.",
        ]

    def _volatile_context_parts(self, ctx_data: ContextData) -> list:
        """The two live data lists (Known Agents / Unread
        Messages) — changed mid-session by bus tools and consumed unreads,
        so they are per-turn volatile. Rendering (caps, order, wording) is
        unchanged from the pre-R4 in-instruction rendering; only the
        destination differs by the relocation flag (R4: relocated, never
        dropped)."""
        parts = []

        # Known agents (capped + filtered)
        known = ctx_data.extra_data.get("bus_known_agents", [])
        if known:
            parts.append("")
            parts.append(f"### Known Agents (top {min(len(known), MAX_KNOWN_AGENTS_IN_CONTEXT)})")
            for a in known[:MAX_KNOWN_AGENTS_IN_CONTEXT]:
                name = inline_field(a.get("agent_name") or a.get("agent_id", ""))
                desc = a.get("agent_description") or a.get("description", "")
                aid = inline_field(a.get("agent_id", ""), None)
                line = f"- `{aid}` — {name}"
                # An unset description is rendered as NOTHING, never as the
                # creation placeholder: printing "a new agent ready for
                # configuration" next to every peer is what left "ask the
                # teaching expert" with nothing to aim at, and made this list
                # read as "none of these agents are usable" (P1 section 02).
                if not is_agent_description_unset(desc):
                    line += f": {inline_field(desc, INLINE_DESCRIPTION_MAX_CHARS)}"
                # `via_team` was computed for every peer and read by nobody.
                # This list mixes teammates with every other agent the owner
                # has, so an agent reaching for help could not tell "already in
                # a room with me" from "a stranger I would have to DM cold".
                if a.get("via_team"):
                    line += " (teammate)"
                parts.append(line)

        # Your teams — the standing address book for `message_team`. Printed
        # with `team_id` (the argument the tool needs), so an agent woken in a
        # DM can still post into a room it belongs to: the id is here regardless
        # of whether that room has recent unread traffic. This is the vocabulary
        # the removed channel list should have used — a team handle the agent
        # can actually pass — not a raw `channel_id`.
        teams = ctx_data.extra_data.get("bus_teams", [])
        if teams:
            parts.append("")
            shown = min(len(teams), MAX_TEAMS_IN_CONTEXT)
            parts.append(f"### Your teams (top {shown})")
            for t in teams[:MAX_TEAMS_IN_CONTEXT]:
                tid = inline_field(t.get("team_id", ""), None)
                name = inline_field(str(t.get("name") or "").strip() or "Team")
                parts.append(f"- `{tid}` — {name}")

        # The channel list that used to sit here is gone on purpose. It printed
        # raw `channel_id`s and a `channel_type` into the agent's context, which
        # is the vocabulary this redesign removes: an agent has private
        # conversations (Known Agents, above) and teams (Your teams, above), and
        # both are already named by handles it can actually use. The list existed
        # to make `read_history` callable — that tool now takes `with_agent` /
        # `team_id`, so nothing needs it.
        # Unread messages (capped, with source tag preview)
        unread = ctx_data.extra_data.get("bus_unread_messages", [])
        if unread:
            # A room's name and team id, per channel that appears in this
            # window. Missing is the honest default and renders the
            # private-conversation form: a room we could not resolve is not
            # evidence of a team.
            rooms = ctx_data.extra_data.get("bus_room_labels") or {}
            rows: list[tuple[str, str]] = []
            for m in unread[:MAX_UNREAD_IN_CONTEXT]:
                room = rooms.get(m.get("channel_id", "")) or {}
                # One row of a multipart message: say so, or a preview of part
                # 2 reads as a message that starts mid-sentence.
                part_label = (
                    f"(part {m.get('part_index')}/{m.get('part_count')}) "
                    if m.get("part_count") else ""
                )
                # `msg_type` is carried so platform lines are labelled rather
                # than quoted as a member. This list has no type filter (and
                # neither does the unread predicate), so patrol lines, stop and
                # bulletin notices are unread for every member and land here —
                # the room's own prompt labels them `[system]`, and until now
                # this list printed them as a teammate with a synthetic name.
                tag = _bus_tag(m.get("from_agent"), room.get("name"), m.get("msg_type"))
                read_call = _read_rest_call(
                    m.get("from_agent"), m.get("msg_type"), room.get("team_id", "")
                )
                rows.append(
                    (_unread_row(tag, m.get("content"), part_label, read_call), read_call)
                )
            # The window is what we render; the total is what the reader needs
            # in order to know a window is what it is looking at. They stopped
            # being the same number when the cap moved into the query.
            total = int(ctx_data.extra_data.get("bus_unread_total") or len(unread))

            # Span budget: admit rows newest first (the window is in reading
            # order, newest last), always keeping the newest one; then give
            # back the oldest kept rows until the not-shown line fits too.
            # Giving a row back can lengthen the notice (the row's call joins
            # its list), so the loop is not monotonic; the call list is bounded
            # (NOT_SHOWN_MAX_CALLS), which bounds that growth, and the loop
            # takes the first count, walking down from the most, whose notice
            # fits. It never goes below the newest row.
            kept = len(rows)
            used = 0
            for i in range(len(rows) - 1, -1, -1):
                size = len(rows[i][0]) + 1
                if i < len(rows) - 1 and used + size > UNREAD_SPAN_MAX_CHARS:
                    kept = len(rows) - 1 - i
                    break
                used += size
            notice = _not_shown_line(total, kept, rows)
            while kept > 1 and used + len(notice) + 1 > UNREAD_SPAN_MAX_CHARS:
                used -= len(rows[len(rows) - kept][0]) + 1
                kept -= 1
                notice = _not_shown_line(total, kept, rows)
            parts.append("")
            parts.append(f"### Unread Messages: {total} (showing {kept})")
            # Same scoping as the static rule above, for the same reason —
            # and it matters more here, because this header sits directly on a
            # list that MIXES team-room messages in. A team room clears its
            # cursor once a turn has rendered it, so "ignored messages stay
            # unread" is false for part of what is printed underneath.
            parts.append(
                "> Remember: apply Reply Discipline. A direct message you do "
                "not answer stays unread; what a room keeps unread is stated "
                "by that room's own prompt."
            )
            if notice:
                parts.append(notice)
            parts.extend(r for r, _ in rows[len(rows) - kept:])

        return parts

    async def contribute_instructions(self, ctx_data: ContextData) -> str:
        """Usage rules, plus (flag OFF only) the live data lists.

        With the R4 relocation flag ON the output is byte-stable across
        turns and the lists travel via contribute_turn_context(); flag OFF keeps
        the legacy single-block rendering, byte-identical to pre-R4.
        """
        parts = self._static_instruction_parts()
        if not settings.prompt_turn_context_relocation_enabled:
            parts = parts + self._volatile_context_parts(ctx_data)
        return "\n".join(parts)

    async def contribute_turn_context(self, ctx_data: ContextData) -> str:
        """Per-turn volatile span: the Known Agents /
        Unread Messages lists, under a stable heading."""
        volatile = self._volatile_context_parts(ctx_data)
        if not volatile:
            return ""
        return "\n".join(["### Who is around, and what is waiting", *volatile])

    # =========================================================================
    # Hooks
    # =========================================================================

    async def _room_labels(self, channel_ids: set) -> dict:
        """``{channel_id: {"name", "team_id"}}`` for the team rooms among
        `channel_ids`. The name labels the row's tag; the ``team_id`` is the
        handle `read_history(team_id=...)` takes, so a cut row can name the
        exact call that returns its full text (the team id is already a handle
        the agent sees in "Your teams", unlike a raw ``channel_id``).

        Bounded by the unread WINDOW, not by how many conversations the agent
        has — the top-N channel list this replaces was a per-turn query for a
        block nobody read, whereas every row this labels is printed.

        Never raises and never guesses: a channel it cannot resolve is simply
        absent, and the renderer falls back to the private-conversation form.
        Mislabelling a private message as a room would be worse than a missing
        label, because the reply disciplines for the two differ.

        Goes through `AsyncDatabaseClient`, NOT `bus._db`. `LocalMessageBus`
        stores the RAW backend verbatim, and the raw SQLite backend hands SQL to
        aiosqlite unmodified — `%s` is not a placeholder there. So these two
        queries raised, the fail-open below swallowed it, and on SQLite the map
        was ALWAYS empty: every team-room message rendered in the
        private-conversation form, which is the one mislabelling the paragraph
        above says must not happen. It worked on MySQL, so it was a 铁律 #7 split
        with the desktop on the broken side, and silent — no exception, nothing
        above debug.

        `team_posting.team_cascade_depth` carries a comment block about this exact
        trap, written two commits before this function, and it did not stop it.
        So, plainly: **a `%s` query belongs to the client, and `bus._db` is not
        one.**
        """
        if not channel_ids:
            return {}
        from narranexus.platform.utils.db.db_factory import get_db_client

        try:
            db = await get_db_client()
            rows = await db.execute(
                "SELECT channel_id, created_by FROM bus_channels "
                f"WHERE channel_id IN ({', '.join(['%s'] * len(channel_ids))})",
                tuple(channel_ids),
                fetch=True,
            )
            rooms = {}
            for r in rows or []:
                owner = str(r.get("created_by") or "")
                if owner.startswith(TEAM_ROOM_OWNER_PREFIX):
                    rooms[r["channel_id"]] = owner[len(TEAM_ROOM_OWNER_PREFIX):]
            if not rooms:
                return {}
            # One set, used for both the placeholder count and the params: two
            # independent constructions feeding a count and its tuple is the
            # shape that breaks when someone edits one line.
            team_ids = tuple(set(rooms.values()))
            team_rows = await db.execute(
                "SELECT team_id, name FROM teams "
                f"WHERE team_id IN ({', '.join(['%s'] * len(team_ids))})",
                team_ids,
                fetch=True,
            )
            names = {r["team_id"]: r["name"] for r in (team_rows or []) if r.get("name")}
            return {
                cid: {"name": names[tid], "team_id": tid}
                for cid, tid in rooms.items()
                if tid in names
            }
        except Exception as e:  # noqa: BLE001 — a label is never worth a turn
            # Warning, not debug: the static block tells the agent its unread
            # queue is tagged by room, so a silently-empty label map mislabels
            # every team-room message as a private one (the disciplines differ).
            logger.warning(f"team room labels failed: {e}")
            return {}

    async def _team_address_book(self, db: Any, team_ids: list) -> list[dict]:
        """`[{"team_id", "name"}]` for the teams the agent belongs to — the
        standing address book `message_team(team_id=...)` needs.

        The PRODUCER, kept separate from `_volatile_context_parts` (the renderer)
        for the reason `_room_labels` is: a renderer test that supplies its own
        `bus_teams` proves the renderer and says nothing about the fetch, and it
        is the fetch that decides whether a DM turn can reach a room at all.

        Takes the ``team_ids`` the caller already resolved for peer scoping, so
        membership is not re-queried — only the team NAMES are read. The cap is
        applied HERE, on the fetch, not only at render time, so a pathological
        owner's team count never rides into ``extra_data`` in full. The ids are
        SORTED before the cap, so which teams survive the truncation is stable
        and reproducible rather than a function of the membership query's row
        order (only matters above the cap, which normal team counts never hit).
        A row whose ``team_id`` went missing is dropped; a row with no name keeps
        its id (the id is the address; the name is only a label).

        Never raises: an address book is not worth a turn (same posture as
        `_room_labels`).
        """
        if not team_ids:
            return []
        try:
            rows = await db.get_by_ids(
                "teams", "team_id", sorted(team_ids)[:MAX_TEAMS_IN_CONTEXT]
            )
            return [
                {"team_id": r.get("team_id", ""), "name": r.get("name") or "Team"}
                for r in (rows or [])
                if r.get("team_id")
            ]
        except Exception as e:  # noqa: BLE001 — an address book is never worth a turn
            # Warning, not debug, and distinct from the wiring failure below:
            # this carries a context PROMISE (the static block says every team
            # is listed), so its silent failure is the core remedy going dark,
            # not optional decoration. Fail-open stays — a book is not worth a
            # turn — but it must be visible.
            logger.warning(f"team address book fetch failed (names query): {e}")
            return []

    async def gather(self, ctx_data: ContextData) -> ContextData:
        """
        Inject MessageBus context into agent data.

        1. Auto-register current agent in bus_agent_registry (idempotent)
        2. Fetch known agents (filtered + capped)
        3. Fetch unread messages (capped)
        4. Fetch channel list (capped)
        5. (nothing) — a step here used to prefix the turn's input with a
           source tag. It was deleted on 2026-08-17 after it turned out never
           to have executed; see the comment where it stood, at the end of this
           method, which names both dead keys. Do not describe an input prefix
           in this contract again without first checking that one exists.
        """
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return ctx_data

            # --- 1. Keep this agent's peer-discovery row true ---
            #
            # Was an inline write with `capabilities=[]` hardcoded and the raw
            # `agent_description` (i.e. the creation placeholder) republished —
            # which is what made `bus_search_agents` answer nothing for every
            # query and told askers that a configured peer was "a new agent
            # ready for configuration" (P1 section 02, all 488 prod rows). The policy
            # now lives in ONE service that creation / rename / config / skill
            # install also call, so discovery no longer waits for a first turn;
            # this call is the idempotent per-turn backstop.
            try:
                db = await _get_shared_db()
                if db:
                    from narranexus.platform.message_bus.agent_discovery_sync import (
                        sync_agent_discovery,
                    )
                    await sync_agent_discovery(db, self.agent_id)
            except Exception as e:
                # Warning, not debug: this is the per-turn backstop for the P1
                # (section 02, 488 prod rows) where discovery went stale and
                # peers were reported as unconfigured. A silent failure lets that
                # P1 recur with only a debug line to show for it.
                logger.warning(f"agent discovery sync failed: {e}")

            # --- 2. Fetch known agents ---
            #
            # Visibility rule (subproject 1, 议题 7 follow-up):
            #
            #   * If THIS agent belongs to one or more teams → only same-team
            #     members (across all of this agent's teams) + public agents.
            #     Same-owner agents NOT in any of my teams are excluded —
            #     team is the messageable boundary once you've opted in.
            #
            #   * If this agent is in NO team → fall back to legacy behavior:
            #     same-owner agents + public. (Backwards-compatible default
            #     for users who haven't created any team.)
            #
            # This way "team" actually scopes who an agent can talk to via
            # the bus; users who don't use teams keep the old "everyone I own"
            # behavior unchanged.
            known_agents = []
            # Resolved inside the known-agents block, but declared out here so
            # the address book below can reuse it without a second membership
            # query — and can still run if the known-agents block fails past
            # this point (see step 2b).
            my_team_ids: list = []
            try:
                db = await _get_shared_db()
                if db:
                    agent_row = await db.get_one("agents", {"agent_id": self.agent_id})
                    my_owner = agent_row.get("created_by", "") if agent_row else ""

                    # Resolve my team-mates (set of agent_ids I share at least
                    # one team with). Empty set → I'm in no team → legacy mode.
                    from narranexus.platform.repository import TeamMemberRepository
                    team_repo = TeamMemberRepository(db)
                    my_team_ids = await team_repo.list_teams_by_agent(self.agent_id)
                    teammate_ids: set = set()
                    if my_team_ids:
                        for mate in await team_repo.list_team_mates(self.agent_id):
                            teammate_ids.add(mate["agent_id"])

                    use_team_scope = bool(my_team_ids)

                    all_agents = await db.get("agents", {})
                    for a in all_agents:
                        aid = a.get("agent_id")
                        if aid == self.agent_id:
                            continue
                        same_owner = my_owner and a.get("created_by") == my_owner
                        is_public = bool(a.get("is_public", 0))
                        if use_team_scope:
                            # In team mode: only team-mates (same owner or not)
                            # plus public agents.
                            if aid not in teammate_ids and not is_public:
                                continue
                        else:
                            # Legacy / no-team mode: same owner OR public.
                            if not (same_owner or is_public):
                                continue
                        known_agents.append({
                            "agent_id": aid,
                            "agent_name": a.get("agent_name", ""),
                            "agent_description": a.get("agent_description", ""),
                            "is_public": a.get("is_public", 0),
                            "created_by": a.get("created_by", ""),
                            # Whether this agent shares a team with us —
                            # rendered as "(teammate)" in the list below.
                            "via_team": aid in teammate_ids,
                        })
                        if len(known_agents) >= MAX_KNOWN_AGENTS_IN_CONTEXT:
                            break
                if known_agents:
                    ctx_data.extra_data["bus_known_agents"] = known_agents
            except Exception as e:
                # Warning, not debug: the static block promises "everyone else
                # you can reach is in your Known Agents list" and builds the
                # "ask another agent" errand on it (the 2026-08-02 P1). A silent
                # empty list reruns that P1 with only a debug line to show for it.
                logger.warning(f"known agents fetch failed (agents scan): {e}")

            # --- 2b. Standing "your teams" address book ---
            #
            # Its OWN failure domain, kept out of the known-agents try above:
            # that block ends in a full-table `agents` scan, and a failure there
            # must not also cost the address book — which is the very thing this
            # change relies on to let a DM turn reach a team room. Reuses the
            # `my_team_ids` resolved above (membership is not re-queried); the
            # producer reads only the team NAMES. Gives `message_team(team_id=)`
            # a target on every turn, not only when a team room sits in the
            # unread window (`bus_room_labels` is windowed and keyed on
            # channel_id).
            try:
                db = await _get_shared_db()
                if db and my_team_ids:
                    bus_teams = await self._team_address_book(db, my_team_ids)
                    if bus_teams:
                        ctx_data.extra_data["bus_teams"] = bus_teams
            except Exception as e:
                # Distinct from the producer's own fetch failure above: this is
                # the wiring around it (getting the shared db). Warning for the
                # same reason — the address book is this change's core remedy.
                logger.warning(f"team address book wiring failed (db handle): {e}")

            # --- 3. Fetch unread messages (capped) ---
            # The cap is pushed into the query, and it selects the NEWEST ones.
            # Slicing an oldest-first list in Python (what this used to do) kept
            # handing the model the most ancient messages in the backlog — and
            # since the read cursor never advanced in team rooms, that window
            # was frozen: the same 20 lines, turn after turn, described as if
            # they were the room's current state.
            try:
                unread = await bus.get_unread(
                    self.agent_id, limit=MAX_UNREAD_IN_CONTEXT
                )
                if unread:
                    ctx_data.extra_data["bus_unread_messages"] = [
                        msg.model_dump() for msg in unread
                    ]
                    # The total is a separate question once the fetch is capped:
                    # len() of a window always equals the window.
                    ctx_data.extra_data["bus_unread_total"] = await bus.count_unread(
                        self.agent_id
                    )
                    ctx_data.extra_data["bus_room_labels"] = await self._room_labels(
                        {m.channel_id for m in unread if m.channel_id}
                    )
            except Exception as e:
                # Warning, not debug: the static block tells the agent its
                # unread messages "are already in this turn's context. You do
                # not need to fetch them." A silent failure drops the inbox while
                # the prompt still says not to go looking for it.
                logger.warning(f"unread fetch failed: {e}")

            # --- 4. (removed) channel list ---
            #
            # A per-turn query used to load this agent's channels here so the
            # instruction could print them. Nothing renders them any more (see
            # the note where that list used to be), and the tool that needed
            # the ids takes handles now — so this was a round-trip per turn for
            # data no reader had.
            # --- 5. (removed) input source tag ---
            #
            # A branch used to sit here prefixing `[MessageBus · sender ·
            # channel]` onto the turn's input. It never ran, and could not have
            # worked if it had:
            #
            #   * its guard read `extra_data["working_source"]`, and nothing in
            #     the repo writes that key — `working_source` is a ContextData
            #     FIELD, seeded at `context_runtime.py:147`, while `extra_data`
            #     is filled from `trigger_extra_data` alone (the correct read is
            #     `working_source_matches(ctx_data.working_source, …)`, which
            #     `expressive_tools` in this same file already does);
            #   * it wrote to `extra_data["input_content"]`, which had no reader
            #     in the codebase. The input the model actually receives is the
            #     FIELD `ctx_data.input_content` (`context_runtime.py:1032`).
            #
            # It is deleted rather than repaired because switching it on is a
            # live change to two shipped prompts — a team turn's input IS the
            # whole `[Team Group Chat]…` prompt and a DM turn's is
            # `_build_prompt`'s output, and both already name their sender in
            # their own words, so a machine prefix in front of them is a product
            # decision, not a bug fix. The instructions above now describe the
            # tags that genuinely reach the model, on the unread list.

        except Exception as e:
            logger.exception(f"MessageBusModule gather failed: {e}")
        return ctx_data

    async def after_turn(
        self, params: HookAfterExecutionParams
    ) -> None:
        """
        Post-execution cleanup for MessageBus.

        Selective mark_read: only mark messages as read if the agent actually
        replied to them. Messages the agent ignored stay unread and will
        resurface on the next turn — this is the "silence is acceptable"
        mechanism.

        We detect replies by inspecting the trace for `message_agent` and
        `message_team` calls. Those names have to track the tools: while they
        still named the pre-2026-08-17 tools, nothing counted as a reply and this
        cursor stopped advancing altogether.
        """
        # Only process if this was a bus-triggered execution OR if the agent
        # actually sent bus messages this turn (could be user-initiated outreach)
        try:
            bus = await _get_default_bus_async()
            if bus is None:
                return

            # Extract channel IDs that were replied to
            replied_channels: set[str] = set()
            replied_agents: set[str] = set()   # peers written to via message_agent
            replied_teams: set[str] = set()    # rooms spoken in via message_team

            if params.trace and params.trace.agent_loop_response:
                for response in params.trace.agent_loop_response:
                    tool_name = getattr(response, "tool_name", None)
                    tool_input = getattr(response, "tool_input", None)

                    # Names arrive either fully qualified
                    # (mcp__message_bus_module__message_agent) or bare, depending
                    # on how the SDK reports them, so match on the substring.
                    #
                    # 2026-08-17 — these MUST track the tool renames. They were
                    # still matching `message_team` / `message_agent`
                    # after both tools were replaced, which meant nothing ever
                    # counted as a reply and this cursor never advanced again:
                    # the same permanently-unread deadlock just fixed on the IM
                    # side, re-introduced on the peer side by a rename.
                    if not tool_name:
                        continue
                    if not isinstance(tool_input, dict):
                        continue

                    if "message_team" in tool_name:
                        # The room's channel follows from the team, and the hook
                        # does not have it — but every unread row in that room is
                        # in the same channel as the reply, so record the team and
                        # resolve it below.
                        tid = tool_input.get("team_id")
                        if tid:
                            replied_teams.add(tid)
                    elif "message_agent" in tool_name:
                        target = tool_input.get("to")
                        if target:
                            replied_agents.add(target)

            # A team we spoke in resolves to its room through the ONE place that
            # composes the marker (`team_rooms.primary_room_of`) — not a hand-
            # rolled fifth copy of the `created_by == marker` query. `primary_room_of`
            # is try/except internally and returns None on a missing db, so the
            # old `if db else None` guard is preserved by its own contract.
            if replied_teams:
                from narranexus.platform.message_bus.team_rooms import primary_room_of

                db = await _get_shared_db()
                for tid in replied_teams:
                    cid = await primary_room_of(db, tid)
                    if cid:
                        replied_channels.add(cid)

            # Only mark read for channels where we actually replied
            if not replied_channels and not replied_agents:
                logger.debug(
                    f"MessageBus: agent {self.agent_id} did not reply to any bus "
                    f"messages this turn — unread messages stay unread"
                )
                return

            # Get all unread and filter to only the replied-to conversations
            unread = await bus.get_unread(self.agent_id)
            if not unread:
                return

            to_mark = []
            for m in unread:
                if m.channel_id in replied_channels:
                    to_mark.append(m.message_id)
                    continue
                # For message_agent: we sent a DM to some agent. Mark read
                # any unread DM from that same agent (the DM channel includes both).
                if m.from_agent in replied_agents:
                    to_mark.append(m.message_id)

            if to_mark:
                await bus.mark_read(self.agent_id, to_mark)
                logger.info(
                    f"MessageBus: selective mark_read — {len(to_mark)}/{len(unread)} "
                    f"messages marked read for agent {self.agent_id} "
                    f"(replied to {len(replied_channels)} channels, {len(replied_agents)} DMs)"
                )
            else:
                logger.debug(
                    f"MessageBus: agent {self.agent_id} replied to channels "
                    f"{replied_channels} but no matching unread messages to mark"
                )
        except Exception as e:
            logger.exception(f"MessageBusModule after_turn failed: {e}")


# =============================================================================
# Module-level helpers
# =============================================================================

_bus_instances: dict[int, Any] = {}  # keyed by event loop id


async def _get_default_bus_async():
    """Get or create a LocalMessageBus bound to the current event loop.

    Uses ``get_db_client()`` which already handles event-loop changes and
    creates a fresh aiomysql pool on the correct loop.  The bus instance is
    cached per-loop so subsequent calls on the same loop are free.
    """
    import asyncio

    loop = asyncio.get_running_loop()
    loop_id = id(loop)

    if loop_id in _bus_instances:
        return _bus_instances[loop_id]

    try:
        from narranexus.platform.message_bus import LocalMessageBus
        from narranexus.platform.utils.db.db_factory import get_db_client

        db = await get_db_client()
        backend = db._backend
        if backend is None:
            logger.warning("MessageBus: database backend is None")
            return None

        bus = LocalMessageBus(backend=backend)
        _bus_instances[loop_id] = bus
        logger.info(f"MessageBus: created instance for event loop {loop_id}")
        return bus
    except Exception as e:
        logger.exception(f"Failed to initialize default MessageBus: {e}")
        return None


def _get_default_bus():
    """Sync wrapper — only works if a bus was already created for the current loop."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return _bus_instances.get(id(loop))
    except RuntimeError:
        return None


async def _get_shared_db():
    """Get the shared AsyncDatabaseClient."""
    try:
        from narranexus.platform.utils.db.db_factory import get_db_client
        return await get_db_client()
    except Exception as e:
        # Warning, not debug: returning None here is the ONE way the callers'
        # `if db:` guards fall through silently (they never reach their own
        # except), so this is where a lost db handle must become visible — the
        # address book / discovery / known-agents that ride on it all go dark
        # otherwise, and the static block still promises them.
        logger.warning(f"shared DB client unavailable: {e}")
        return None
