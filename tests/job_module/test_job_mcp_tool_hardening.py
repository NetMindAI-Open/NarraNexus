"""
@file_name: test_job_mcp_tool_hardening.py
@author: Bin Liang
@date: 2026-08-04
@description: job_create / job_update boundary hardening (W1).

Two live-reproduced defects:
1. job_create was the ONLY tool in the file with no try/except around its
   body — setup_mcp_llm_context raising LLMConfigNotConfigured reached the
   model as a raw exception string it read as "impossible", instead of a
   structured error telling it how to self-correct.
2. trigger_config was a bare `dict` — the model got zero structure hints
   (everything rode on docstring prose), and a stringified JSON payload
   crashed deep in the service as "argument of type 'str' is not a mapping".
   A typed shape (inline properties, no $ref, no anyOf-null) documents the
   fields in the schema itself and rejects malformed input at the boundary
   with a message the model can act on.
"""
from unittest.mock import AsyncMock, patch

import pytest

from narranexus.platform.agent_framework.api_config import LLMConfigNotConfigured
from narranexus_plugins.job_module import create_job_from_args
from narranexus_plugins.job_module._job_mcp_tools import create_job_mcp_server

# setup_mcp_llm_context is imported inside create_job_from_args (the shared
# helper that now owns the structured-error handling), so patch it at the source
# module, not at the tool module.
API_MOD = "narranexus.platform.agent_framework.api_config"


def _server():
    return create_job_mcp_server()


def _tool(mcp, name):
    return {t.name: t for t in mcp._tool_manager.list_tools()}[name]


# ---------------------------------------------------------------------------
# trigger_config schema shape
# ---------------------------------------------------------------------------

def _trigger_schema(tool):
    return tool.parameters["properties"]["trigger_config"]


@pytest.mark.parametrize("tool_name", ["job_create", "job_update"])
def test_trigger_config_is_an_inline_object_schema(tool_name):
    """Strict providers must not have to resolve JSON Schema composition."""
    tool = _tool(_server(), tool_name)
    schema = _trigger_schema(tool)

    assert schema.get("type") == "object", schema
    assert "properties" in schema, schema
    assert "$ref" not in schema, schema
    assert "anyOf" not in schema, schema
    assert "$defs" not in tool.parameters, tool.parameters


def test_job_create_trigger_config_declares_properties():
    tool = _tool(_server(), "job_create")
    schema = _trigger_schema(tool)
    assert "properties" in schema, f"bare object schema: {schema}"
    keys = set(schema["properties"])
    assert {"timezone", "run_at", "cron", "interval_seconds", "end_condition"} <= keys


def test_job_update_trigger_config_declares_properties():
    tool = _tool(_server(), "job_update")
    schema = _trigger_schema(tool)
    assert "properties" in schema, f"bare object schema: {schema}"
    assert "cron" in schema["properties"]


def test_trigger_config_optional_fields_have_plain_types():
    """NotRequired fields must be plain-typed (absent = unset), never
    anyOf[X, null] — the exact shape strict providers reject."""
    tool = _tool(_server(), "job_create")
    schema = _trigger_schema(tool)
    for name, prop in schema["properties"].items():
        assert "anyOf" not in prop, f"{name} is anyOf-typed: {prop}"
        assert prop.get("type"), f"{name} has no explicit type: {prop}"


@pytest.mark.parametrize("tool_name", ["job_create", "job_update"])
def test_trigger_config_description_is_model_facing(tool_name):
    description = _trigger_schema(_tool(_server(), tool_name))["description"]

    assert "IANA timezone" in description
    assert len(description) < 200
    assert "TypedDict" not in description
    assert "Google" not in description


def test_job_update_trigger_config_has_no_invalid_null_default():
    schema = _trigger_schema(_tool(_server(), "job_update"))
    assert "default" not in schema, schema


# ---------------------------------------------------------------------------
# job_create structured failure instead of raw exceptions
#
# The W1 hardening (no raw exception ever reaches the model) now lives in the
# shared create_job_from_args helper — the mcp tool is a thin seam delegate —
# so these tests target the helper directly, where the try/except that owns the
# structured-error contract lives.
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llm_config_failure_returns_structured_error():
    with patch(
        f"{API_MOD}.setup_mcp_llm_context",
        AsyncMock(side_effect=LLMConfigNotConfigured("Cannot resolve LLM config for agent_id=agent_current")),
    ):
        result = await create_job_from_args(
            object(),
            "agent_current",
            user_id="user_1",
            title="T",
            description="d",
            job_type="one_off",
            trigger_config={"run_at": "2026-09-01T09:00:00", "timezone": "Asia/Shanghai"},
            payload="p",
        )
    assert isinstance(result, dict)
    assert result["success"] is False
    # The error must be actionable: point the model at its instructions.
    assert "instructions" in result["error"].lower()


@pytest.mark.asyncio
async def test_unexpected_exception_returns_structured_error():
    with patch(
        f"{API_MOD}.setup_mcp_llm_context",
        AsyncMock(side_effect=RuntimeError("boom")),
    ):
        result = await create_job_from_args(
            object(),
            "agent_1",
            user_id="user_1",
            title="T",
            description="d",
            job_type="one_off",
            trigger_config={"run_at": "2026-09-01T09:00:00", "timezone": "Asia/Shanghai"},
            payload="p",
        )
    assert result == {"success": False, "error": "boom"}


@pytest.mark.asyncio
async def test_job_create_tool_surfaces_seam_error_not_raises(monkeypatch):
    # W1 at the tool layer: job_create now delegates to the seam, which NEVER
    # raises (DirectStore try/except + HttpStore degradation). Prove the tool
    # returns that structured error verbatim rather than throwing it at the model.
    class _FakeStore:
        async def job_create(self, agent_id, fields):
            return {"success": False, "error": "structured, not an exception"}

    monkeypatch.setattr(
        "narranexus_plugins.job_module._job_mcp_tools.get_agent_data_store",
        lambda *a, **k: _FakeStore(),
    )
    fn = _tool(_server(), "job_create").fn
    result = await fn(
        agent_id="agent_1", user_id="u1", title="T", description="d",
        job_type="one_off",
        trigger_config={"run_at": "2026-09-01T09:00:00", "timezone": "UTC"},
        payload="p",
    )
    assert result == {"success": False, "error": "structured, not an exception"}


@pytest.mark.asyncio
async def test_missing_timezone_reaches_job_create_structured_error(db_client):
    with patch(f"{API_MOD}.setup_mcp_llm_context", AsyncMock()):
        result = await create_job_from_args(
            db_client,
            "agent_1",
            user_id="user_1",
            title="Missing timezone",
            description="d",
            job_type="scheduled",
            trigger_config={"cron": "0 9 * * *"},
            payload="p",
        )

    assert result["success"] is False
    assert "Invalid trigger_config" in result["error"]
    assert "timezone" in result["error"]
