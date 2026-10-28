"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: Kernel — the part of NarraNexus without which no process starts.

Sub-packages: ``plugins`` (registry / hooks / slots / bindings / manifest /
loader), ``events`` (host event bus), plus ``deployment`` (the single
cloud-vs-local resolver). Nothing here knows any concrete plugin.
"""


class Kernel:
    """Contract of the ``kernel`` root slot: the kernel itself is never rebound."""


__all__ = ["Kernel"]
