"""
@file_name: test_agents_row_writers_normalize.py
@author: NarraNexus
@date: 2026-08-17
@description: Every writer of the `agents` row stores normalized text.

`agent_field_matches` compares normalized values, so the predicate is only
sound while the row HOLDS normalized text. A row created with " 小绿 " can
never be cleaned up: renaming it to "小绿" compares equal, issues no write, and
the endpoint then certifies the untouched row as correct — success, no error,
nothing in the logs. The failure is invisible by construction, which is why the
invariant is pinned per writer rather than trusted.

`AgentRepository` covers the routes, the MCP tool, arena provisioning and the
migration applier. The paths below raw-write the table and therefore normalize
at their own edge; re-derive the list with

    git grep -nE '(insert|update)\\(\\s*"agents"|_ins\\("agents"' -- backend src
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.platform.repository import AgentRepository
from narranexus.platform.schema import AGENT_TEXT_FIELDS, normalize_agent_row_text
from narranexus.platform.schema.entity_schema import AGENT_TEXT_MAX_LENGTH

PADDED = "  小绿  "
CLEAN = "小绿"


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class TestSharedNormalizer:
    def test_only_text_columns_are_touched(self):
        out = normalize_agent_row_text(
            {
                "agent_name": PADDED,
                "agent_description": "  d  ",
                "created_by": "  alice  ",
                "is_public": 1,
            }
        )
        assert out == {
            "agent_name": CLEAN,
            "agent_description": "d",
            "created_by": "  alice  ",  # not a text column of this rule
            "is_public": 1,
        }

    def test_the_field_set_is_what_the_predicate_dispatches_on(self):
        """One definition. If these drift, a column ends up normalized but
        compared raw (or the reverse) and "already equal" stops meaning
        anything."""
        assert AGENT_TEXT_FIELDS == frozenset({"agent_name", "agent_description"})

    def test_the_input_is_not_mutated(self):
        src = {"agent_name": PADDED}
        normalize_agent_row_text(src)
        assert src == {"agent_name": PADDED}


class TestManyfoldWriteEdge:
    """POST /manyfold/agents (insert + idempotent update) and PATCH — three
    raw writes, and PATCH is a rename endpoint where unstripped input from a
    UI is ordinary."""

    def test_the_request_models_strip_before_the_cap_is_measured(self):
        import backend.routes.manyfold.agents as mf

        at_limit_padded = "y" * AGENT_TEXT_MAX_LENGTH + "   "
        assert (
            mf.ManyfoldCreateAgentRequest(
                agent_id="a", manyfold_user_id="u", agent_name=at_limit_padded
            ).agent_name
            == "y" * AGENT_TEXT_MAX_LENGTH
        )
        assert (
            mf.ManyfoldUpdateAgentRequest(
                agent_description=at_limit_padded
            ).agent_description
            == "y" * AGENT_TEXT_MAX_LENGTH
        )

    def test_the_patch_body_normalizes_the_new_name(self):
        import backend.routes.manyfold.agents as mf

        assert mf.ManyfoldUpdateAgentRequest(agent_name=PADDED).agent_name == CLEAN


class TestRepositoryEdge:
    def test_a_created_row_is_still_renameable(self, db_client):
        repo = AgentRepository(db_client)
        _run(repo.add_agent(agent_id="agent_r1", agent_name=PADDED, created_by="u1"))
        stored = _run(repo.get_agent("agent_r1"))
        assert stored.agent_name == CLEAN, (
            "an unstripped stored name is unrenameable: the update path would "
            "compare the stripped form as equal and never write"
        )


class TestManyfoldUpsertFallback:
    """The one branch the wrapper around the upsert is solely responsible for.

    `StrippedText` already normalizes whatever the CALLER supplies, so
    `normalize_agent_row_text` there earns its keep only on the fallback: an
    omitted field falls back to the value already in the row, and a row written
    before normalization existed (or by a pre-fix import) can be unstripped.
    Without the wrapper that stale value is written straight back.
    """

    @pytest.fixture
    def client(self, db_client, monkeypatch):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import backend.routes.manyfold.agents as mf

        async def _db():
            return db_client

        monkeypatch.setattr(mf, "get_db_client", _db)
        monkeypatch.setattr(mf, "_require_manyfold_auth", lambda _request: None)
        app = FastAPI()
        app.include_router(mf.router)
        return TestClient(app)

    def test_an_omitted_name_rewrites_the_existing_value_normalized(
        self, client, db_client
    ):
        _run(
            db_client.insert(
                "agents",
                {
                    "agent_id": "agent_mf_fb",
                    "agent_name": " old ",
                    "created_by": "mf_u1",
                    "agent_description": " d ",
                    "agent_type": "general",
                    "is_public": 0,
                },
            )
        )

        res = client.post(
            "/manyfold/agents",
            json={"agent_id": "agent_mf_fb", "manyfold_user_id": "u1"},
        )
        assert res.status_code == 200, res.text

        row = _run(db_client.get_one("agents", {"agent_id": "agent_mf_fb"}))
        assert row["agent_name"] == "old", (
            "the fallback wrote the stale unstripped value straight back — "
            "that row can never be renamed afterwards"
        )
        assert row["agent_description"] == "d"
