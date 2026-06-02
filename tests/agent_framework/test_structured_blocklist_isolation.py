"""
@file_name: test_structured_blocklist_isolation.py
@author: Bin Liang
@date: 2026-05-29
@description: A2 regression guards for the structured-output blocklist.

Two invariants the blocklist MUST hold (incident lesson #3):
  1. It is keyed by (base_url, model) so the same model name on a
     different provider is judged independently.
  2. Only a clear "unsupported response_format" error is a blocklist
     trigger — transient network / 5xx errors are NOT.
"""

from xyz_agent_context.agent_framework import openai_agents_sdk as oa
from xyz_agent_context.agent_framework.openai_agents_sdk import (
    _capability_key,
    _is_response_format_unsupported_error,
)


def test_capability_key_includes_base_url(monkeypatch):
    monkeypatch.setattr(oa.openai_config, "base_url", "https://api.netmind.ai/v1")
    key_a = _capability_key("deepseek-chat")
    monkeypatch.setattr(oa.openai_config, "base_url", "https://api.openai.com/v1")
    key_b = _capability_key("deepseek-chat")
    # Same model name, different provider -> different keys (no contamination).
    assert key_a != key_b
    assert key_a[1] == key_b[1] == "deepseek-chat"


def test_capability_key_normalises_trailing_slash(monkeypatch):
    monkeypatch.setattr(oa.openai_config, "base_url", "https://x.ai/v1/")
    assert _capability_key("m")[0] == "https://x.ai/v1"


def test_capability_errors_are_blocklist_triggers():
    assert _is_response_format_unsupported_error(
        Exception("this response_format type is unavailable")
    )
    assert _is_response_format_unsupported_error(
        Exception("json_schema is unsupported for this model")
    )


def test_transient_errors_are_not_blocklist_triggers():
    # These must NOT be treated as capability errors, so they never blocklist.
    for transient in [
        "Connection reset by peer",
        "Read timed out",
        "503 Service Unavailable",
        "rate limit exceeded",
        "Internal Server Error",
    ]:
        assert not _is_response_format_unsupported_error(Exception(transient)), transient


def test_blocklist_holds_tuple_keys():
    # Type contract: blocklist stores (base_url, model) tuples, not bare names.
    oa._structured_output_blocklist.add(("https://api.netmind.ai/v1", "m"))
    try:
        assert ("https://api.netmind.ai/v1", "m") in oa._structured_output_blocklist
        assert "m" not in oa._structured_output_blocklist  # bare name never matches
    finally:
        oa._structured_output_blocklist.discard(("https://api.netmind.ai/v1", "m"))
