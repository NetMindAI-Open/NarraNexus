"""
@file_name: test_port_preflight_ports_sync.py
@author: NetMind.AI
@date: 2026-07-27
@description: Anti-rot guard — the Rust desktop port preflight's hardcoded
    REQUIRED_PORTS list must cover every MCP port the Python side actually
    binds (module_runner.all_module_ports()).

Why this exists: `tauri/src-tauri/src/sidecar/port_preflight.rs` keeps a
hand-maintained REQUIRED_PORTS array so it can detect + auto-clean orphaned
sidecars before Tauri's runtime exists. That array is a copy of the Python
source of truth and has drifted before (a channel module / new core module
gets a port, nobody updates Rust, the preflight silently stops covering it,
and orphaned sidecars on the missing ports leak `[Errno 48] address already
in use` on the next launch). This test fails the moment the two diverge.
"""

from __future__ import annotations

import pathlib
import re

from xyz_agent_context.module.module_runner import all_module_ports

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PREFLIGHT_RS = _REPO_ROOT / "tauri/src-tauri/src/sidecar/port_preflight.rs"


def _rust_required_ports() -> set[int]:
    """Parse the integer literals inside the Rust REQUIRED_PORTS array."""
    text = _PREFLIGHT_RS.read_text(encoding="utf-8")
    m = re.search(r"REQUIRED_PORTS[^=]*=\s*&\[(.*?)\];", text, re.S)
    assert m, "REQUIRED_PORTS array not found in port_preflight.rs"
    body = re.sub(r"//.*", "", m.group(1))  # strip line comments before parsing
    return {int(x) for x in re.findall(r"\b\d+\b", body)}


def test_required_ports_cover_every_mcp_port():
    rust_ports = _rust_required_ports()
    mcp_ports = set(all_module_ports().values())
    missing = mcp_ports - rust_ports
    assert not missing, (
        "port_preflight.rs REQUIRED_PORTS is missing MCP ports "
        f"{sorted(missing)} — orphaned sidecars on these ports would go "
        "undetected on next launch. Add them to REQUIRED_PORTS."
    )
