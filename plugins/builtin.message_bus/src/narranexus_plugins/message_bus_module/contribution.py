"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-07
@description: What ``builtin.message_bus`` contributes to the host, as the objects its manifest names. The plugin owns this table; the platform holds no list of builtins.
"""
from __future__ import annotations

from narranexus.contracts.channel import MessageSourceSpec
from narranexus.platform.channel.contributions import message_source_contribution, module_contribution

PLUGIN_ID = "builtin.message_bus"
MODULES = (module_contribution("narranexus_plugins.message_bus_module.message_bus_module:MessageBusModule", PLUGIN_ID, channel=False),)

# The bus is a message source but NOT a channel, so it contributes a standalone
# ``ingress.message_sources`` entry. ``reply_tools`` carries every tool that
# DELIVERS on a bus turn: the peer and room sends (answer-the-peer / team
# replies) and ``notify_owner`` (Owner Relay). Its live consumer is
# ``ChatModule._origin_delivered_text`` — the [DELIVERED-BG]/[NO-REPLY-BG]
# persistence split whose counts are the no-reply metric behind the
# delivery-fallback decision. Listing only the owner-chat tool recorded genuine
# bus deliveries as NO-REPLY (2026-08-01) and poisoned exactly that metric.
MESSAGE_SOURCES = (
    message_source_contribution(MessageSourceSpec(
        name="message_bus",
        # Not "Message Bus" — the bus is infrastructure the agent never sees.
        # What it sees is that it is inside NarraNexus talking to a peer agent
        # or a team; WHICH of those is answered by the tool on its desk.
        display_label="NarraNexus",
        reply_tools=("notify_owner", "message_agent", "message_team"),
        # Bus sends deliver to peer AGENTS — nothing appears in the owner's web
        # chat. Only the owner-notify tool is owner-visible, so session anchoring
        # and chat-history persistence ignore agent-to-agent traffic (see
        # MessageSourceHandler.owner_visible_reply_tool_names).
        owner_visible_reply_tools=("notify_owner",),
        # Not "[Bus · …]": the word names the transport, which is the third
        # concept spec §8 removes from the agent's vocabulary — it has private
        # conversations and teams, and every other source's prefix names the
        # PLACE the message came from rather than the pipe it arrived through.
        row_prefix_template="[private message from {from_agent}]",
    )),
)

__all__ = ["MESSAGE_SOURCES", "MODULES", "PLUGIN_ID"]
