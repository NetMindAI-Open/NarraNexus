"""
@file_name: test_hook_manager_parallel_import.py
@author:
@date: 2026-08-14
@description: The parallel data_gathering path must actually be executable.

``HookManager(parallel_data_gathering=True)`` is the documented fast path,
but nothing in the tree enables it, so its body was never executed by any
test — and it shipped importing ``ContextDataMerger`` from a module path
that does not exist (the class lives in ``_module_impl.ctx_merger``).
Same latent-``NameError``/``ImportError`` class as the 2026-08-14 lark-cli
outage: a lazily-executed branch referencing something that resolves for
no interpreter. This test drives the branch for real so the import breaks
the suite instead of the first caller who flips the flag.
"""
from __future__ import annotations

import pytest

from narranexus.platform.module_system.hook_manager import HookManager
from narranexus.platform.schema.context_schema import ContextData


class _EchoModule:
    """Minimal stand-in: returns its (copied) ctx untouched."""

    class config:  # noqa: D106 — mirrors XYZBaseModule.config.name access
        name = "EchoModule"

    async def gather(self, ctx: ContextData) -> ContextData:
        return ctx


class _BoomModule:
    """Stand-in for a module whose data_gathering raises."""

    class config:  # noqa: D106 — mirrors XYZBaseModule.config.name access
        name = "BoomModule"

    async def gather(self, ctx: ContextData) -> ContextData:
        raise RuntimeError("gathering blew up")


@pytest.mark.asyncio
async def test_parallel_data_gathering_path_is_executable():
    mgr = HookManager(parallel_data_gathering=True)
    ctx = ContextData(agent_id="agent_x", user_id=None, input_content="hi")
    result = await mgr.gather([_EchoModule()], ctx)
    assert isinstance(result, ContextData)
    assert result.agent_id == "agent_x"


@pytest.mark.asyncio
async def test_parallel_data_gathering_error_branch_is_executable():
    """The error branch (DataGatheringError construction + structured log)
    is just as lazily-executed as the import was — drive it for real: a
    raising module must be dropped without failing the turn, and the
    healthy module's result must still be merged."""
    mgr = HookManager(parallel_data_gathering=True)
    ctx = ContextData(agent_id="agent_x", user_id=None, input_content="hi")
    result = await mgr.gather([_BoomModule(), _EchoModule()], ctx)
    assert isinstance(result, ContextData)
    assert result.agent_id == "agent_x"
