"""
@file_name: test_lark_reaction_identity.py
@author: NarraNexus
@date: 2026-07-10
@description: LarkCLIClient.add_reaction must run ``--as bot`` so the emoji
reaction is attributed to the agent's bot, not the owner's user OAuth identity.

Regression: the raw ``im reactions create`` command defaults to the user token
when the workspace holds one (after the three-click flow), so the 👍 showed up
under the human owner's name instead of the bot.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.lark_module.lark_cli_client import LarkCLIClient


@pytest.mark.asyncio
async def test_add_reaction_runs_as_bot(monkeypatch):
    cli = LarkCLIClient()
    captured: dict = {}

    async def _fake_run(args, agent_id, *a, **k):
        captured["args"] = args
        captured["agent_id"] = agent_id
        return {"success": True, "data": {"reaction_id": "r_1"}}

    monkeypatch.setattr(cli, "_run_with_agent_id", _fake_run)

    rid = await cli.add_reaction("agent_a", "om_msg", "Typing")

    assert rid == "r_1"
    args = captured["args"]
    assert args[:3] == ["im", "reactions", "create"]
    # explicit bot identity is present
    assert "--as" in args
    assert args[args.index("--as") + 1] == "bot"
