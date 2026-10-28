"""
@file_name: contribution.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.frameworks.nexus_power — the agent-loop framework contribution (driver factory).
"""
from __future__ import annotations

from narranexus.contracts.framework import FrameworkMeta
from narranexus.kernel.plugins.registry import Contribution


def _factory(**factory_kwargs):
    from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import NexusAgent

    return NexusAgent(**factory_kwargs)


# NexusPower drives the provider API itself (protocol "any") and refuses
# subscription credentials outright (no oauth_source), so it cannot reach the
# image's shared CLI credential file — ``uses_shared_cli_login=False`` is what
# makes the cloud gate let a non-staff user select it, derived rather than
# spelled out in a platform-side name table.
#
# ``capabilities`` is the static twin of ``NexusAgent.capabilities()``: the
# hosts that must answer "can this framework be steered / replayed natively"
# BEFORE a driver exists (the remote executor shell, history projection) read
# it from here. Keep the two in step — the driver's own method is the runtime
# authority.
META = FrameworkMeta(
    "nexus_power",
    "NexusPower",
    protocol="any",
    runtime_name="NexusPower-beta",
    capabilities=frozenset({"event_log", "steering", "native_replay"}),
    uses_shared_cli_login=False,
)
CONTRIBUTION = Contribution("nexus_power", lambda: _factory, meta={"framework": META})

__all__ = ["CONTRIBUTION", "META"]
