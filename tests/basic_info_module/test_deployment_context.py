"""
@file_name: test_deployment_context.py
@author: Bin Liang
@date: 2026-04-20
@description: BasicInfoModule injects deployment-mode context into the
agent's system prompt (Bug 5 step 2/4).

The agent needs to know whether it's running on a shared cloud server or
the user's own machine — the two modes have fundamentally different
filesystem / global-install / credential semantics, and the rest of the
rule system (SkillModule, _tool_policy_guard) will key off this.

`BasicInfoModule.hook_data_gathering` populates two fields on
``ContextData``:

  - ``deployment_mode`` — "cloud" | "local" — short tag
  - ``deployment_context`` — verbose description block rendered into
    the system prompt via the ``{deployment_context}`` placeholder

These tests pin the contract.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.basic_info_module.basic_info_module import (
    BasicInfoModule,
)
from xyz_agent_context.module.basic_info_module.prompts import (
    BASIC_INFO_MODULE_INSTRUCTIONS,
    DEPLOYMENT_CONTEXT_CLOUD,
    DEPLOYMENT_CONTEXT_LOCAL,
)
from xyz_agent_context.schema import ContextData
from xyz_agent_context.utils.deployment_mode import DEPLOYMENT_MODE_ENV_VAR


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/test.db")
    yield


# -------- prompt constants exist & explain both modes -----------------


def test_both_deployment_context_blocks_exist_and_differentiate_modes():
    """Sanity: the two constants describe what each mode means in
    plain-language terms so the agent can reason about them."""
    for block in (DEPLOYMENT_CONTEXT_CLOUD, DEPLOYMENT_CONTEXT_LOCAL):
        assert "cloud" in block.lower()
        assert "local" in block.lower()

    # The cloud block tells the agent about workspace containment.
    assert "workspace" in DEPLOYMENT_CONTEXT_CLOUD.lower()
    # The local block acknowledges the user's own machine.
    assert "own" in DEPLOYMENT_CONTEXT_LOCAL.lower() or "user's" in DEPLOYMENT_CONTEXT_LOCAL.lower()

    # Cloud is strict about global installs, local allows them.
    assert "not" in DEPLOYMENT_CONTEXT_CLOUD.lower() and "global" in DEPLOYMENT_CONTEXT_CLOUD.lower()
    assert "allowed" in DEPLOYMENT_CONTEXT_LOCAL.lower() or "may" in DEPLOYMENT_CONTEXT_LOCAL.lower()


def test_instructions_template_has_deployment_placeholder():
    """The top-level agent prompt template must reference the placeholder
    (otherwise the hook's work never reaches the system prompt)."""
    assert "{deployment_context}" in BASIC_INFO_MODULE_INSTRUCTIONS


# -------- hook populates ctx_data -------------------------------------


async def _run_hook(mode_env_value: str | None, monkeypatch, db_client):
    """Helper: run BasicInfoModule.hook_data_gathering under a given mode."""
    if mode_env_value is None:
        monkeypatch.delenv(DEPLOYMENT_MODE_ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(DEPLOYMENT_MODE_ENV_VAR, mode_env_value)

    # Seed minimal agent row so hook's DB lookup doesn't fail.
    await db_client.insert(
        "agents",
        {
            "agent_id": "agent_deploytest",
            "agent_name": "Test Bot",
            "created_by": "owner_user",
            "agent_type": "general",
            "is_public": 0,
        },
    )

    module = BasicInfoModule(
        agent_id="agent_deploytest",
        user_id="owner_user",
        database_client=db_client,
    )
    ctx = ContextData(
        agent_id="agent_deploytest",
        user_id="owner_user",
        input_content="hi",
    )
    return await module.hook_data_gathering(ctx)


@pytest.mark.asyncio
async def test_hook_populates_cloud_mode(db_client, monkeypatch):
    ctx = await _run_hook("cloud", monkeypatch, db_client)
    assert ctx.deployment_mode == "cloud"
    assert ctx.deployment_context == DEPLOYMENT_CONTEXT_CLOUD


@pytest.mark.asyncio
async def test_hook_populates_local_mode_by_default(db_client, monkeypatch):
    ctx = await _run_hook(None, monkeypatch, db_client)
    assert ctx.deployment_mode == "local"
    assert ctx.deployment_context == DEPLOYMENT_CONTEXT_LOCAL


@pytest.mark.asyncio
async def test_hook_explicit_local(db_client, monkeypatch):
    ctx = await _run_hook("local", monkeypatch, db_client)
    assert ctx.deployment_mode == "local"


# -------- rendered system prompt contains the block -------------------


def _with_render_extras(ctx):
    """``context_runtime`` sets two extra placeholders at render time
    that aren't on the ContextData schema proper. Stub them in the
    tests so the top-level template's ``.format()`` doesn't KeyError."""
    setattr(ctx, "agent_info_model_type", "Claude Agent SDK")
    setattr(ctx, "model_name", "test-model")
    return ctx


@pytest.mark.asyncio
async def test_rendered_prompt_contains_cloud_block_when_cloud(
    db_client, monkeypatch
):
    ctx = _with_render_extras(await _run_hook("cloud", monkeypatch, db_client))
    module = BasicInfoModule(
        agent_id="agent_deploytest",
        user_id="owner_user",
        database_client=db_client,
    )
    rendered = await module.get_instructions(ctx)

    # Cloud-specific language is present, local is not
    assert "CLOUD" in rendered or "cloud" in rendered
    # The specific "workspace" restriction phrase must reach the agent
    assert "workspace" in rendered.lower()


@pytest.mark.asyncio
async def test_rendered_prompt_contains_local_block_when_local(
    db_client, monkeypatch
):
    ctx = _with_render_extras(
        await _run_hook(None, monkeypatch, db_client)
    )  # default → local
    module = BasicInfoModule(
        agent_id="agent_deploytest",
        user_id="owner_user",
        database_client=db_client,
    )
    rendered = await module.get_instructions(ctx)
    assert "local" in rendered.lower()
    # Local language acknowledges the user's own machine.
    assert ("own computer" in rendered.lower()
            or "own machine" in rendered.lower()
            or "user's machine" in rendered.lower())
