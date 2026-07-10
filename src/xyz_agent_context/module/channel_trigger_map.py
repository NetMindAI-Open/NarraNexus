"""
@file_name: channel_trigger_map.py
@author: NetMind.AI
@date: 2026-07-08
@description: Single source of truth mapping channel name -> IM trigger class.

Why this file exists
====================
Every IM channel trigger is a ``ChannelTriggerBase`` subclass living under
``module/<channel>_module/<channel>_trigger.py``. The consolidated supervisor
(``run_channel_triggers``) needs the full set up-front to instantiate and
``start()`` each in ONE process/event-loop, replacing the old "one OS process
per channel" layout.

This map lives in the ``module`` layer (NOT ``channel``) on purpose: the trigger
subclasses live here, and ``channel`` is a LOWER layer — importing the subclasses
from ``channel`` would invert the dependency direction and re-enter the circular
import that ``channel_trigger_base`` already documents (module -> channel ->
runtime -> module). The supervisor is a top-level entrypoint, so it is free to
import from ``module``.

Defensive import (per-channel isolation)
========================================
The classes are imported one-by-one from ``_TRIGGER_SPECS`` rather than with
top-level ``import`` statements. In the old one-process-per-channel layout a
channel whose optional dependency was missing (e.g. ``matrix-nio`` for the
NarraMessenger Matrix adapter) only broke ITS OWN process; the other five ran
fine. A consolidated map built with eager top-level imports would let one
channel's ImportError take down ALL channels. So a failed import is logged and
skipped — the supervisor comes up with the channels that DID load. This extends
the supervisor's per-channel startup isolation down to import time.

``_TRIGGER_SPECS`` is the registration INTENT (independent of which optional
deps happen to be installed in this env); the guard test
(``tests/channel/test_trigger_startup_alignment.py``) checks it against the
``ChannelTriggerBase`` subclasses discovered on disk, so a channel shipped
without being registered here still fails CI even if its dep is absent locally.

The key is DERIVED from each class's own ``channel_name`` so the map key and the
class attribute can never drift. Adding a channel = add one line to
``_TRIGGER_SPECS``.
"""

from __future__ import annotations

import importlib

from loguru import logger

from xyz_agent_context.channel.channel_trigger_base import ChannelTriggerBase


# (module_path, class_name) — registration intent. Add a new channel here.
# The "narramessenger" channel is served by the Direct-Matrix adapter
# (matrix_trigger.MatrixTrigger, channel_name="narramessenger"); the old gateway
# NarramessengerTrigger was retired.
_TRIGGER_SPECS: tuple[tuple[str, str], ...] = (
    ("xyz_agent_context.module.lark_module.lark_trigger", "LarkTrigger"),
    ("xyz_agent_context.module.slack_module.slack_trigger", "SlackTrigger"),
    ("xyz_agent_context.module.telegram_module.telegram_trigger", "TelegramTrigger"),
    ("xyz_agent_context.module.discord_module.discord_trigger", "DiscordTrigger"),
    ("xyz_agent_context.module.wechat_module.wechat_trigger", "WeChatTrigger"),
    ("xyz_agent_context.module.narramessenger_module.matrix_trigger", "MatrixTrigger"),
)

# Class names of every registered channel trigger — the registration INTENT,
# independent of whether each channel's optional dependency is installed here.
# The guard test checks on-disk ChannelTriggerBase subclasses against this set.
REGISTERED_TRIGGER_CLASS_NAMES: frozenset[str] = frozenset(
    name for _, name in _TRIGGER_SPECS
)


def _load_trigger_classes() -> dict[str, type[ChannelTriggerBase]]:
    """Import each spec defensively; skip (with a warning) any that fail.

    A channel whose optional dependency is missing must not take down the whole
    supervisor — it is dropped and the rest still load.
    """
    loaded: dict[str, type[ChannelTriggerBase]] = {}
    for module_path, cls_name in _TRIGGER_SPECS:
        try:
            module = importlib.import_module(module_path)
            cls = getattr(module, cls_name)
        except Exception as e:  # noqa: BLE001 — missing dep / import error in one channel
            logger.warning(
                f"channel trigger {cls_name} unavailable, skipped "
                f"({type(e).__name__}: {e})"
            )
            continue
        loaded[cls.channel_name] = cls
    return loaded


# name -> class. Only the channels that imported successfully in this env.
CHANNEL_TRIGGER_MAP: dict[str, type[ChannelTriggerBase]] = _load_trigger_classes()
