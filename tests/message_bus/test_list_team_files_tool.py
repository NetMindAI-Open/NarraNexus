"""
@file_name: test_list_team_files_tool.py
@author: NarraNexus
@date: 2026-08-07
@description: Agents can enumerate the team folder instead of being told.

Before the index there was no way to ask what a team had shared: the folder
holds files named by generated id, so the only discovery channel was another
agent reciting an absolute path in the room. That made "did you get the file"
a social protocol between models — it worked exactly as often as the models
remembered to narrate.

Membership is checked server-side against team_members. agent_id arrives from
the identity headers, but the membership check is what actually bounds this:
one user owns many teams, so being the owner's agent is not permission to read
any of that owner's teams.
"""

from __future__ import annotations

import pytest

from narranexus.platform.message_bus.team_files import list_team_files

OWNER = "user_1"
TEAM = "team_1"


async def _seed(db, *, agent="agent_a", member=True, files=("a.md", "b.md")):
    await db.insert("agents", {"agent_id": agent, "agent_name": agent, "created_by": OWNER})
    await db.insert("teams", {"team_id": TEAM, "owner_user_id": OWNER, "name": "T"})
    if member:
        await db.insert("team_members", {"team_id": TEAM, "agent_id": agent})
    for i, name in enumerate(files):
        await db.insert("team_files", {
            "file_id": f"f{i}", "team_id": TEAM, "owner_user_id": OWNER,
            "shared_by_agent_id": "agent_b", "original_name": name,
            "rel_path": f"p/f{i}", "size_bytes": 10, "content_hash": f"h{i}",
        })


@pytest.mark.asyncio
async def test_member_sees_the_teams_files(db_client):
    await _seed(db_client)
    res = await list_team_files(db=db_client, agent_id="agent_a", team_id=TEAM)

    assert res["success"] is True
    assert {f["name"] for f in res["files"]} == {"a.md", "b.md"}


@pytest.mark.asyncio
async def test_entries_carry_a_path_the_agent_can_read(db_client):
    """A listing the agent cannot act on is just prose — each entry must give
    the path Read accepts, plus who shared it."""
    await _seed(db_client, files=("a.md",))
    res = await list_team_files(db=db_client, agent_id="agent_a", team_id=TEAM)

    entry = res["files"][0]
    assert entry["path"].endswith("p/f0")
    assert entry["shared_by"] == "agent_b"
    assert entry["size_bytes"] == 10


@pytest.mark.asyncio
async def test_non_member_is_refused(db_client):
    """The boundary is membership, not ownership: same owner, not in the team,
    no access."""
    await _seed(db_client, member=False)
    res = await list_team_files(db=db_client, agent_id="agent_a", team_id=TEAM)

    assert res["success"] is False
    assert "member" in res["error"].lower()
    assert "files" not in res


@pytest.mark.asyncio
async def test_unknown_team_is_refused(db_client):
    await _seed(db_client)
    res = await list_team_files(db=db_client, agent_id="agent_a", team_id="team_nope")
    assert res["success"] is False


@pytest.mark.asyncio
async def test_empty_folder_is_a_success_not_an_error(db_client):
    """"Nothing shared yet" is an answer. Returning an error would push the
    model toward retrying or apologising for a working tool."""
    await _seed(db_client, files=())
    res = await list_team_files(db=db_client, agent_id="agent_a", team_id=TEAM)

    assert res["success"] is True
    assert res["files"] == []


# ── The MCP tool wrapper itself ─────────────────────────────────────────────
#
# Everything above calls the impl directly, which is exactly how the wrapper
# shipped with an unresolvable `get_db_client` (NameError on 100% of calls)
# while this file stayed green. This test goes through the registered tool.


class _FakeMCP:
    """Collects the registered tools so tests can call them directly."""

    def __init__(self):
        self.tools = {}

    def tool(self, *a, **kw):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn

        return deco


@pytest.mark.asyncio
async def test_mcp_tool_wrapper_resolves_its_own_db(db_client, monkeypatch):
    from narranexus_plugins.message_bus_module import (
        _message_bus_mcp_tools as mod,
    )
    from narranexus.platform.utils.db import db_factory

    await _seed(db_client)

    async def _get_db():
        return db_client

    monkeypatch.setattr(db_factory, "get_db_client", _get_db)

    async def _get_bus():
        raise AssertionError("team_list_files must not need the bus service")

    mcp = _FakeMCP()
    mod.register_message_bus_mcp_tools(mcp, _get_bus)
    res = await mcp.tools["team_list_files"]("agent_a", TEAM)

    assert res["success"] is True
    assert {f["name"] for f in res["files"]} == {"a.md", "b.md"}
