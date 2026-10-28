"""
@file_name: test_port_preflight_ports_sync.py
@author: NetMind.AI
@date: 2026-07-27
@description: Anti-rot guard — the Rust desktop port preflight's hardcoded
    REQUIRED_PORTS list must equal the ports the Python side actually binds:
    backend, sqlite proxy, the ONE module MCP host port (plugin platform batch
    5a: every module server is mounted by path under it, no module owns a
    port) and the Lark trigger health endpoint.
Why this exists: `tauri/src-tauri/src/sidecar/port_preflight.rs` keeps a
hand-maintained REQUIRED_PORTS array so it can detect + auto-clean orphaned
sidecars before Tauri's runtime exists. That array is a copy of the Python
source of truth and has drifted before. This test fails the moment the two
diverge — in either direction: a port the Python side no longer binds must
leave the array too, or the preflight would refuse to launch over a port
NarraNexus does not use.
"""
from __future__ import annotations

import pathlib
import re

from narranexus.platform.module_system.base import mcp_port

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_PREFLIGHT_RS = _REPO_ROOT / "tauri/src-tauri/src/sidecar/port_preflight.rs"

BACKEND_PORT = 8000
SQLITE_PROXY_PORT = 8100
LARK_HEALTH_PORT = 47831  # channel/_health_server.py


def _rust_required_ports() -> set[int]:
    """Parse the integer literals inside the Rust REQUIRED_PORTS array."""
    text = _PREFLIGHT_RS.read_text(encoding="utf-8")
    m = re.search(r"REQUIRED_PORTS[^=]*=\s*&\[(.*?)\];", text, re.S)
    assert m, "REQUIRED_PORTS array not found in port_preflight.rs"
    body = re.sub(r"//.*", "", m.group(1))  # strip line comments before parsing
    return {int(x) for x in re.findall(r"\b\d+\b", body)}


def test_required_ports_equal_the_ports_python_binds(monkeypatch):
    monkeypatch.delenv("MCP_PORT", raising=False)
    assert _rust_required_ports() == {BACKEND_PORT, SQLITE_PROXY_PORT, mcp_port(), LARK_HEALTH_PORT}


def test_no_module_owns_a_port():
    """The per-module port table is gone: no module class or spec carries one."""
    import narranexus.platform.module_system as mod

    for name in mod.module_registry:
        assert not hasattr(mod.module_registry[name], "mcp_port"), name
