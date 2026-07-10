"""
@file_name: test_bus_channel_inbox_skip.py
@author: Bin Liang
@date: 2026-07-03
@description: Guard — MessageBusTrigger must never re-dispatch channel-inbox rows.

ChannelInboxWriter persists every IM turn to ``bus_messages`` under
``channel_id = f"{channel}_{chat_id}"`` purely for history/Inbox display; the
channel's own trigger already ran AgentRuntime for it. The bus trigger used a
hand-maintained prefix tuple ("lark_", "telegram_", "slack_") to skip those
rows, so wechat/narramessenger/discord turns were consumed AGAIN: a second
agent run per message wearing the Owner-Relay peer-agent prompt, which
fabricated wechat_send context_tokens and sent bogus platform DMs
(dev incident 2026-07-03, agent_0ed73ae78099).

The skip set is now derived from MessageSourceRegistry: any handler that
declares ``dedicated_trigger=True`` owns its ``{name}_`` inbox prefix. The
filesystem is the guard's source of truth — every ``run_<name>_trigger.py``
channel entrypoint must have a registered dedicated-trigger handler.
"""

from pathlib import Path

# Importing the module package registers every module's MessageSourceHandler
# (module/__init__.py builds MODULE_MAP by importing all module packages).
import xyz_agent_context.module  # noqa: F401
from xyz_agent_context.channel.message_source_handler import MessageSourceRegistry
from xyz_agent_context.message_bus.message_bus_trigger import im_channel_prefixes

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_DIR = REPO_ROOT / "src" / "xyz_agent_context" / "module"


def _channel_names_from_entrypoints() -> list[str]:
    """``run_<name>_trigger.py`` → ``<name>`` for every channel module."""
    return sorted(
        path.stem[len("run_"):-len("_trigger")]
        for path in MODULE_DIR.glob("*_module/run_*_trigger.py")
    )


def test_every_channel_trigger_declares_dedicated_handler():
    handlers = MessageSourceRegistry.handlers()
    missing = [
        name
        for name in _channel_names_from_entrypoints()
        if name not in handlers or not handlers[name].dedicated_trigger
    ]
    assert not missing, (
        "Channel modules with a run_*_trigger entrypoint must register a "
        "MessageSourceHandler with dedicated_trigger=True, or the bus trigger "
        f"re-dispatches their inbox rows as new messages: {missing}"
    )


def test_im_channel_prefixes_cover_every_channel_trigger():
    prefixes = im_channel_prefixes()
    missing = [
        name
        for name in _channel_names_from_entrypoints()
        if f"{name}_" not in prefixes
    ]
    assert not missing, f"bus skip-prefixes missing channels: {missing}"


def test_wechat_inbox_channel_id_matches_skip_prefixes():
    channel_id = "wechat_o9cq8059Chjp8rgLbpVL15acKiAo@im.wechat"
    assert channel_id.startswith(im_channel_prefixes())


def test_non_channel_ids_do_not_match():
    for channel_id in ("bus_agent_x", "job_123", "chat_room_9", "wechatless_x"):
        assert not channel_id.startswith(im_channel_prefixes()), channel_id
