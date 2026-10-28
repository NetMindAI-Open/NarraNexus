"""
@file_name: module_runner.py
@author: Bin Liang
@date: 2026-09-04
@description: By-path entrypoint shim (one release): ``python src/xyz_agent_context/module/module_runner.py mcp`` runs the MCP host from ``narranexus.platform.module_system.module_runner``.

The deploy repo's compose (``command: … src/xyz_agent_context/module/module_runner.py mcp``)
and older desktop builds launch this path; it delegates to ``main()`` of the
moved module. Removed with the ``xyz_agent_context`` alias next release.
"""
from narranexus.platform.module_system.module_runner import main

if __name__ == "__main__":
    raise SystemExit(main())
