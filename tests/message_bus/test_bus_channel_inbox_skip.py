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
filesystem is the guard's source of truth — every ``*_module/`` package with a
``ChannelTriggerBase`` subclass must have a registered dedicated-trigger
handler.

Batch 6 moved every channel module from ``src/narranexus/platform/module_system``
to ``plugins/builtin.channels.*/src/narranexus_plugins/<name>_module/`` — and
the trigger file itself is no longer named ``run_<name>_trigger.py`` (e.g.
NarraMessenger's is ``matrix_trigger.py``, not ``run_narramessenger_trigger.py``).
So the channel name has to come from the *module directory* (``<name>_module``)
that contains a ``ChannelTriggerBase`` subclass, not from the trigger
filename, and the scan has to cover every plugin's ``src/`` (see
``tests/_paths.py``).
"""

import re

# Importing the module package registers every module's MessageSourceHandler
# (module/__init__.py builds module_registry by importing all module packages).
import narranexus.platform.module_system  # noqa: F401
from narranexus.platform.channel.message_source_handler import MessageSourceRegistry
from narranexus.platform.message_bus.message_bus_trigger import im_channel_prefixes

from tests._paths import engine_source_roots

# ``class Foo(ChannelTriggerBase):`` — on-disk source of truth for "this
# module directory owns a channel trigger" (mirrors test_trigger_startup_alignment.py).
_SUBCLASS_RE = re.compile(r"class\s+\w+\s*\(\s*ChannelTriggerBase\s*\)")


def _channel_names_from_entrypoints() -> list[str]:
    """``<name>_module/`` → ``<name>`` for every module dir owning a
    ``ChannelTriggerBase`` subclass, across every plugin's ``src/``."""
    names: set[str] = set()
    for root in engine_source_roots():
        for path in root.glob("narranexus_plugins/*_module/*_trigger.py"):
            if "__pycache__" in path.parts:
                continue
            if not _SUBCLASS_RE.search(path.read_text(encoding="utf-8")):
                continue
            module_dir = path.parent.name  # "<name>_module"
            names.add(module_dir[: -len("_module")])
    return sorted(names)


def test_channel_name_discovery_finds_the_known_channels():
    """Sanity: the filesystem scan finds the known channels (guards the glob).

    Without this, a rename that breaks the glob silently shrinks the scan to
    zero and both real guards below start passing for the wrong reason.
    """
    names = _channel_names_from_entrypoints()
    assert "lark" in names
    assert "wechat" in names
    assert len(names) >= 6


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
