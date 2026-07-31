"""
@file_name: test_agents_sdk_compat.py
@author:
@date: 2026-07-30
@description: Guard against openai-agents / openai SDK version skew.

openai 2.x made ``InputTokensDetails.cache_write_tokens`` a REQUIRED
field; openai-agents builds that object all over its model layer
(``Usage()`` defaults, chat-completions usage mapping, stream handler).
When the two packages drift apart, every Agents SDK call dies with a
ValidationError before the response reaches the caller — on dev this
made 100% of helper structured-output calls pay a failed SDK attempt
and drop to the fallback path (2026-07-30, gpt-5.4-mini).

These tests construct the exact objects the SDK builds at runtime, so
a resolver picking incompatible versions fails CI instead of prod.
"""

from agents.usage import Usage


def test_usage_default_construction_matches_installed_openai_sdk():
    usage = Usage()
    assert usage.input_tokens_details.cached_tokens == 0


def test_usage_accepts_provider_payload_without_cache_write_tokens():
    # The wire shape gateways like NetMind actually send: chat-completions
    # PromptTokensDetails carries cached_tokens ONLY, never
    # cache_write_tokens. This is the exact payload that blew up under
    # the 0.5.0/2.50.0 skew.
    from openai.types.completion_usage import PromptTokensDetails

    usage = Usage(
        requests=1,
        input_tokens=10,
        output_tokens=2,
        total_tokens=12,
        input_tokens_details=PromptTokensDetails(cached_tokens=0),
        output_tokens_details=None,
    )
    assert usage.input_tokens_details.cache_write_tokens == 0
