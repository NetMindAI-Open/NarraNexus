"""
@file_name: test_create_agent_empty_name_parity.py
@author: NarraNexus
@date: 2026-08-17
@description: Both create-agent paths refuse a blank name with the SAME string.

`create_agent` exists twice — the DirectStore seam (local) and the
`/social-network/create-agent` route (cloud) — and the pair is required to be
byte-identical, because the model reads whichever one its deployment happens to
use. `CREATE_AGENT_EMPTY_NAME_MSG` is shared for exactly that reason.

The gap this pins: the route's body model had `min_length=1`, so an empty name
was a 422 *before* the route's own check ran, and the model was handed a
transport failure string on one path and the shared constant on the other — for
the same tool call. Whitespace-only never had that problem (it passes
`min_length`, then normalizes to ""), so only the true-empty branch split.
"""
from __future__ import annotations

import asyncio

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from narranexus_plugins.social_network_module import (
    CREATE_AGENT_EMPTY_NAME_MSG,
    CREATE_AGENT_TEXT_TOO_LONG_MSG,
    create_agent_text_reject,
    default_created_by_description,
)
from narranexus.platform.schema.entity_schema import AGENT_TEXT_MAX_LENGTH
from narranexus.platform.repository import AgentRepository
from narranexus.platform.repository.user_repository import UserRepository

OWNER = "alice"
CALLER = "agent_caller00"
NEW_ID = "agent_0123456789ab"

BLANK_NAMES = ["", "   "]

OVER = "x" * (AGENT_TEXT_MAX_LENGTH + 1)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def seeded(db_client):
    async def _seed():
        await UserRepository(db_client).add_user(user_id=OWNER, user_type="individual")
        await AgentRepository(db_client).add_agent(
            agent_id=CALLER, agent_name="caller", created_by=OWNER
        )

    _run(_seed())


@pytest.fixture
def route_client(db_client, monkeypatch, seeded):
    import narranexus_plugins.social_network_module.routes as sn

    async def _db():
        return db_client

    monkeypatch.setattr(sn, "get_db_client", _db)

    async def _allow(_request, _agent_id):
        return None

    monkeypatch.setattr(sn, "require_agent_owner", _allow)  # the sdk.web seam name

    app = FastAPI()

    @app.middleware("http")
    async def fake_auth(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(sn.router, prefix="/api/agents")
    return TestClient(app)


@pytest.mark.parametrize("blank", BLANK_NAMES)
def test_the_route_refuses_with_the_shared_message(route_client, db_client, blank):
    res = route_client.post(
        f"/api/agents/{CALLER}/social-network/create-agent",
        json={"new_agent_id": NEW_ID, "agent_name": blank},
        headers={"X-User-Id": OWNER},
    )
    assert res.status_code == 200, (
        f"a blank name must reach the handler, not 422 out of the body model "
        f"({res.status_code}); otherwise the model gets a transport string "
        f"instead of {CREATE_AGENT_EMPTY_NAME_MSG!r}"
    )
    body = res.json()
    assert body["success"] is False
    assert body["error"] == CREATE_AGENT_EMPTY_NAME_MSG
    # Asserted HERE, in the test that actually triggered the refusal: the
    # db_client fixture is function-scoped, so a standalone "no row exists"
    # test would pass against a database nothing had touched. This one fails
    # if the check ever moves after provision_new_agent.
    assert _run(AgentRepository(db_client).get_agent(NEW_ID)) is None


@pytest.mark.parametrize("blank", BLANK_NAMES)
def test_the_direct_store_refuses_with_the_same_message(db_client, seeded, blank):
    from narranexus.platform.module_system.data_access.store import DirectStore

    store = DirectStore()
    # DirectStore resolves its own db through the MCP factory; point it at the
    # test client so both twins run against the same rows.
    store._db = lambda: _async(db_client)  # type: ignore[method-assign]
    out = _run(
        store.create_agent(
            creator_agent_id=CALLER,
            new_agent_id=NEW_ID,
            agent_name=blank,
            awareness="",
            agent_description="",
        )
    )
    assert out["success"] is False
    assert out["message"] == CREATE_AGENT_EMPTY_NAME_MSG
    assert _run(AgentRepository(db_client).get_agent(NEW_ID)) is None


async def _async(value):
    return value


# --- Over-length, the other value both legs must answer identically ----------
# The route's body model deliberately carries no AGENT_TEXT_MAX_LENGTH cap: a
# 422 would arrive before the handler and hand the calling model "invalid
# arguments…", while the DirectStore leg answered with whatever `Agent(...)`
# raised inside add_agent. Both legs now refuse with one shared string that
# names the limit, so the model can shorten and retry either way.


@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_the_route_refuses_overlong_with_the_shared_message(
    route_client, db_client, field
):
    payload = {"new_agent_id": NEW_ID, "agent_name": "n", field: OVER}
    res = route_client.post(
        f"/api/agents/{CALLER}/social-network/create-agent",
        json=payload,
        headers={"X-User-Id": OWNER},
    )
    assert res.status_code == 200, (
        f"an over-long {field} must reach the handler, not 422 out of the body "
        f"model ({res.status_code}) — otherwise the model gets a transport "
        f"string instead of {CREATE_AGENT_TEXT_TOO_LONG_MSG!r}"
    )
    body = res.json()
    assert body["success"] is False
    assert body["error"] == CREATE_AGENT_TEXT_TOO_LONG_MSG
    assert _run(AgentRepository(db_client).get_agent(NEW_ID)) is None


@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_the_direct_store_refuses_overlong_with_the_same_message(
    db_client, seeded, field
):
    from narranexus.platform.module_system.data_access.store import DirectStore

    store = DirectStore()
    store._db = lambda: _async(db_client)  # type: ignore[method-assign]
    kwargs = {"agent_name": "n", "agent_description": "", field: OVER}
    out = _run(
        store.create_agent(
            creator_agent_id=CALLER, new_agent_id=NEW_ID, awareness="", **kwargs
        )
    )
    assert out["success"] is False
    assert out["message"] == CREATE_AGENT_TEXT_TOO_LONG_MSG
    assert _run(AgentRepository(db_client).get_agent(NEW_ID)) is None


@pytest.mark.parametrize("blank", ["   " * 200])
def test_a_long_run_of_spaces_answers_empty_not_overlong_on_both_legs(
    route_client, db_client, blank
):
    from narranexus.platform.module_system.data_access.store import DirectStore

    res = route_client.post(
        f"/api/agents/{CALLER}/social-network/create-agent",
        json={"new_agent_id": NEW_ID, "agent_name": blank},
        headers={"X-User-Id": OWNER},
    )
    assert res.json()["error"] == CREATE_AGENT_EMPTY_NAME_MSG

    store = DirectStore()
    store._db = lambda: _async(db_client)  # type: ignore[method-assign]
    out = _run(
        store.create_agent(
            creator_agent_id=CALLER,
            new_agent_id=NEW_ID,
            agent_name=blank,
            awareness="",
            agent_description="",
        )
    )
    assert out["message"] == CREATE_AGENT_EMPTY_NAME_MSG


# --- The rule itself, now the single source both legs call --------------------
# The per-leg tests above prove the two legs agree. These prove WHAT they agree
# on, including the ordering that only shows up on one input.


@pytest.mark.parametrize(
    "name,description,expected",
    [
        ("n", "d", None),
        ("n", "", None),
        ("", "d", CREATE_AGENT_EMPTY_NAME_MSG),
        # Whitespace has already been normalized away by the caller, so a
        # blank run arrives here as "" — an empty name, never an over-long one.
        ("", OVER, CREATE_AGENT_EMPTY_NAME_MSG),
        (OVER, "d", CREATE_AGENT_TEXT_TOO_LONG_MSG),
        ("n", OVER, CREATE_AGENT_TEXT_TOO_LONG_MSG),
        ("y" * AGENT_TEXT_MAX_LENGTH, "y" * AGENT_TEXT_MAX_LENGTH, None),
    ],
)
def test_the_shared_rule(name, description, expected):
    assert create_agent_text_reject(name, description) == expected


def test_empty_is_checked_before_length():
    """The one input that distinguishes the two orders. Reversed, an empty name
    submitted alongside an over-long description would be reported as
    over-long — on both legs, consistently, and wrongly."""
    assert (
        create_agent_text_reject("", OVER) == CREATE_AGENT_EMPTY_NAME_MSG
    ), "length was checked before empty"


def test_the_default_description_is_clamped_not_refused():
    """It is assembled after the checks, from a name that may itself sit near
    the ceiling, so it is truncated — refusing a string the caller never typed
    could not be explained to them."""
    out = default_created_by_description("N" * AGENT_TEXT_MAX_LENGTH)
    assert len(out) == AGENT_TEXT_MAX_LENGTH
    assert out.startswith("Agent created by N")
