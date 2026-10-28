"""
@file_name: test_nexus_turn_profile.py
@date: 2026-08-06
@description: NexusAgent payload — TurnProfile application + default equivalence.

Locks:
- No turn_profile in kwargs -> options carry prompt_mode "full" (the
  value the assembler previously hardwired), llm_extra untouched,
  include_arg_deltas absent (TurnOptions default engages) — i.e. the
  payload is semantically identical to the pre-TurnProfile build.
- voice_fast profile -> reasoning_effort lands in llm_extra (the litellm
  passthrough), include_arg_deltas is forced on, prompt_mode stays full.
- A model_dump() dict (the wire form crossing the executor boundary)
  behaves identically to the in-process model object.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.frameworks_nexus_power.adapter.nexus_agent import (
    NexusAgent,
    claude_config,
)
from narranexus.platform.schema.turn_profile import TurnProfile


@pytest.fixture()
def anthropic_slot(monkeypatch):
    monkeypatch.setattr(claude_config, "model", "deepseek-v4-flash")
    monkeypatch.setattr(claude_config, "api_key", "k")
    monkeypatch.setattr(claude_config, "base_url", "http://gw.local")
    monkeypatch.setattr(claude_config, "auth_type", "api_key")
    monkeypatch.setattr(claude_config, "thinking", "")


def _payload(**kwargs):
    agent = NexusAgent(working_path="/tmp")
    return agent._build_request_payload(
        messages=[{"role": "user", "content": "hi"}],
        mcp_servers={},
        extra_env=None,
        kwargs=kwargs,
    )


def test_no_profile_is_todays_payload(anthropic_slot):
    options = _payload(agent_id="a1")["options"]
    assert options["prompt_mode"] == "full"
    assert "reasoning_effort" not in options["llm_extra"]
    assert "include_arg_deltas" not in options
    assert "turn_profile" not in options


def test_voice_fast_profile_applies_knobs(anthropic_slot):
    options = _payload(agent_id="a1", turn_profile=TurnProfile.voice_fast())["options"]
    assert options["prompt_mode"] == "full"
    assert options["llm_extra"]["reasoning_effort"] == "low"
    assert options["include_arg_deltas"] is True
    # Voice turns opt in to the mute-turn repair: an unanswered voice
    # turn is never the right outcome.
    assert options["expression_nudge"] is True


def test_steerable_follows_the_registered_steer_channel(anthropic_slot):
    # The UPSTREAM half of the wait-tool gate (C1). The driver stamps
    # options.steerable from whether a live steer channel was handed in — the
    # SAME `steering` kwarg that keeps the runner's stdin open (nexus_agent
    # _open_steer_transport) and that _run_inprocess builds its inlet from. The
    # downstream gate tests (test_steering_wiring) construct TurnOptions
    # directly and never exercise THIS line, so without this a regression here
    # (steering renamed, kwargs not threaded, flag forced) leaves CI green while
    # wait_for_input silently (dis)appears in production. Pin both arms at the
    # source. `is`, not `==`: this must stay a real bool.
    assert _payload(agent_id="a1")["options"]["steerable"] is False
    assert _payload(agent_id="a1", steering=object())["options"]["steerable"] is True


def test_wire_dict_form_behaves_like_model(anthropic_slot):
    profile = TurnProfile(prompt_mode="minimal", reasoning_effort="minimal")
    for form in (profile, profile.model_dump()):
        options = _payload(agent_id="a1", turn_profile=form)["options"]
        assert options["prompt_mode"] == "minimal"
        assert options["llm_extra"]["reasoning_effort"] == "minimal"
