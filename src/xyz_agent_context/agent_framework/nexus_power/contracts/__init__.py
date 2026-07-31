"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-07-29
@description: L0 contract layer — pure data types and Protocols; the
bottom of the package's one-way import flow.

Layering (imports point strictly downward):

    contracts (this layer; stdlib + pydantic only)
      <- _nexus_power_impl/* component groups (import contracts only;
         groups never import each other)
      <- _nexus_power_impl/loop.py (contracts only; components injected)
      <- assembly.py (the single default wiring point)
      <- adapters/nexus driver (the only place touching platform seams)

This layer never imports the rest of the package nor any platform code
(module/, narrative/, agent_runtime/, backend/). Anything two groups
share lives here.
"""
