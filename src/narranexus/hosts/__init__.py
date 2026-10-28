"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: Host boot sequences — how a backend / mcp / workers process brings the plugin platform up.
"""
# No re-exports on purpose: ``narranexus.hosts.boot`` must stay the MODULE
# (re-exporting the ``boot`` function here would shadow it for monkeypatching
# and for ``import narranexus.hosts.boot as boot_mod``).
__all__: list[str] = []
