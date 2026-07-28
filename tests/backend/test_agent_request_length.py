"""
@file_name: test_agent_request_length.py
@author: NetMind.AI
@date: 2026-07-23
@description: Write-edge length validation for agent name/description.

The Create/Update agent request models cap agent_name and agent_description at
AGENT_TEXT_MAX_LENGTH so an over-long value is rejected at the API boundary
(422) instead of being written raw and only failing later on read. Companion
to the import-side trimming (tests/bundle/test_agent_field_length.py) — both
close the #71 gap where the write path bypassed the read-model's ceiling.
"""

import pytest
from pydantic import ValidationError

from xyz_agent_context.schema.entity_schema import AGENT_TEXT_MAX_LENGTH
from xyz_agent_context.schema.api_schema import CreateAgentRequest, UpdateAgentRequest

OVER = "x" * (AGENT_TEXT_MAX_LENGTH + 1)
AT_LIMIT = "y" * AGENT_TEXT_MAX_LENGTH


@pytest.mark.parametrize("model", [CreateAgentRequest, UpdateAgentRequest])
@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_overlong_rejected(model, field):
    with pytest.raises(ValidationError):
        model(**{field: OVER})


@pytest.mark.parametrize("model", [CreateAgentRequest, UpdateAgentRequest])
@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_at_limit_accepted(model, field):
    obj = model(**{field: AT_LIMIT})
    assert getattr(obj, field) == AT_LIMIT


@pytest.mark.parametrize("model", [CreateAgentRequest, UpdateAgentRequest])
def test_none_accepted(model):
    # Both fields are optional — omitting them stays valid.
    obj = model()
    assert obj.agent_name is None
    assert obj.agent_description is None


# --- Manyfold write path (the 4th path — review finding #2) --------------------
# These raw-write the `agents` row, so they must honor the same ceiling; the
# description field used to allow 2000 chars, re-creating the #71 unreadable row.
from backend.routes.manyfold.agents import (  # noqa: E402
    ManyfoldCreateAgentRequest,
    ManyfoldUpdateAgentRequest,
)


@pytest.mark.parametrize("field", ["agent_name", "description"])
def test_manyfold_create_overlong_rejected(field):
    with pytest.raises(ValidationError):
        ManyfoldCreateAgentRequest(
            agent_id="a", manyfold_user_id="u", **{field: OVER}
        )


@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_manyfold_update_overlong_rejected(field):
    with pytest.raises(ValidationError):
        ManyfoldUpdateAgentRequest(**{field: OVER})


@pytest.mark.parametrize("field", ["agent_name", "agent_description"])
def test_manyfold_update_at_limit_accepted(field):
    obj = ManyfoldUpdateAgentRequest(**{field: AT_LIMIT})
    assert getattr(obj, field) == AT_LIMIT
