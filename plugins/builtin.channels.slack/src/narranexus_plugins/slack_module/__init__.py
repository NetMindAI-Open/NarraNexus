"""
@file_name: __init__.py
@date: 2026-05-08
@description: Slack channel module package — re-exports SlackModule.
"""
from narranexus.platform.channel.contributions import register_working_source

from .descriptor import DESCRIPTOR

# The class bodies below read ``WorkingSource.SLACK`` at DEFINITION time, so this
# package guarantees its own source exists before defining them. Idempotent, and
# the same one call the boot path makes (``contributions_from``) — the channel
# names its own source, in exactly one function, and never from a READ of the
# channel map. Importing this package registers nothing else: the message-source
# handler is projected from DESCRIPTOR by the registry view, not registered here.
register_working_source(DESCRIPTOR)


# SlackModule is added once Task 8 (slack_module.py) is in place.
try:
    from .slack_module import SlackModule

    __all__ = ["SlackModule"]
except ImportError:  # pragma: no cover — bootstrap during dependency build-out
    __all__ = []
