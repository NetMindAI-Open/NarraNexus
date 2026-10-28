"""
@file_name: test_executor_protocol_turn_profile.py
@date: 2026-08-06
@description: turn_profile must cross the executor wire explicitly.

Locks: build_agent_loop_request carries turn_profile as a plain dict
(None when absent) — the whitelist-style body means a missing key is a
silent cloud-side drop, which is exactly the failure this pins against.
"""
from __future__ import annotations

from narranexus.platform.agent_runtime.executor_protocol import build_agent_loop_request


def _body(**kw):
    return build_agent_loop_request(
        framework="nexus_power",
        working_path="/tmp",
        messages=[],
        mcp_servers={},
        extra_env=None,
        **kw,
    )


def test_absent_profile_serializes_as_none():
    assert _body()["turn_profile"] is None


def test_profile_dict_rides_the_body():
    tp = {"name": "voice_fast", "prompt_mode": "full"}
    assert _body(turn_profile=tp)["turn_profile"] == tp
