"""
@file_name: test_resolve_owner_split.py
@author:
@date: 2026-08-10
@description: resolve_owner's ""/None split (PR #258 review #4): "" = the
agent does not exist, None = the lookup itself failed. Security callers make
different decisions on the two (404 vs 5xx); truthiness-only callers see both
as falsy and are unaffected.
"""
from __future__ import annotations

import asyncio

from narranexus.platform.repository import AgentRepository


class _Db:
    def __init__(self, row=None, boom=False):
        self._row, self._boom = row, boom

    async def get_one(self, table, filters):
        if self._boom:
            raise RuntimeError("db down")
        return self._row


def test_known_agent_returns_owner():
    repo = AgentRepository(_Db(row={"created_by": "usr_1"}))
    assert asyncio.run(repo.resolve_owner("agent_a")) == "usr_1"


def test_unknown_agent_returns_empty_string():
    repo = AgentRepository(_Db(row=None))
    assert asyncio.run(repo.resolve_owner("agent_a")) == ""


def test_failed_lookup_returns_none_not_empty():
    repo = AgentRepository(_Db(boom=True))
    assert asyncio.run(repo.resolve_owner("agent_a")) is None


def test_empty_agent_id_is_unknown_not_failure():
    repo = AgentRepository(_Db(boom=True))  # db never reached
    assert asyncio.run(repo.resolve_owner("")) == ""
