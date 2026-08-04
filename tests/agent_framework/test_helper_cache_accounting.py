"""Helper SDKs must record the three Anthropic token buckets separately.

Before 2026-07-30 both helper paths collapsed uncached + cache-write +
cache-read into the single ``input_tokens`` column and passed no cache
counters at all. Two consequences, both load-bearing:

  * a cache-warm helper call was priced as if none of it was cached
    (cache reads bill at 0.1x, so up to ~10x overstated); and
  * any future work to actually enable caching on this path would have been
    unverifiable — the ledger would print the same number whether the cache
    hit or missed.

``agent_loop`` (step_4) has always written the three buckets apart. These
tests hold the helper paths to the same shape.
"""

import pytest

from xyz_agent_context.agent_framework.llm.cli_helper import HelperUsage


# =========================================================================
# CliHelperSDK
# =========================================================================

def test_helper_usage_defaults_to_all_zero():
    u = HelperUsage()
    assert (u.input_tokens, u.output_tokens) == (0, 0)
    assert (u.cache_creation_tokens, u.cache_read_tokens) == (0, 0)
    assert u.any_recorded is False


def test_a_fully_cached_call_counts_as_reported_usage():
    """input_tokens == 0 with cache reads is a real, billable call.

    The old guard was `in_tok > 0 or out_tok > 0`. A call served entirely
    from cache can report zero uncached input, so that guard would have
    dropped the row AND fired a spurious "provider returned no usage"
    warning — losing exactly the rows that prove caching works.
    """
    u = HelperUsage(input_tokens=0, output_tokens=0, cache_read_tokens=50_000)
    assert u.any_recorded is True


def test_cache_write_alone_also_counts():
    assert HelperUsage(cache_creation_tokens=1).any_recorded is True


@pytest.mark.asyncio
async def test_cli_helper_forwards_every_bucket_to_the_ledger(monkeypatch):
    import contextvars

    from xyz_agent_context.agent_framework.api_config import (
        ClaudeConfig, CliHelperConfig, OpenAIConfig, set_user_config,
    )
    from xyz_agent_context.agent_framework.llm import cli_helper as cli_mod
    from xyz_agent_context.agent_framework.llm.cli_helper import CliHelperSDK

    recorded: list[dict] = []

    async def _fake_record_cost(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(cli_mod, "record_cost", _fake_record_cost)
    monkeypatch.setattr(
        cli_mod, "get_cost_context", lambda: ("agent_x", object())
    )

    sdk = CliHelperSDK()

    async def _fake_oneshot(system_prompt, user_input, model_name):
        return "plain reply", HelperUsage(
            input_tokens=12, output_tokens=34,
            cache_creation_tokens=56, cache_read_tokens=78,
        )

    monkeypatch.setattr(sdk, "_run_oneshot", _fake_oneshot)

    def _install():
        set_user_config(
            ClaudeConfig(), OpenAIConfig(),
            cli_helper=CliHelperConfig(framework="claude_code"),
        )
    contextvars.copy_context().run(_install)
    _install()

    await sdk.llm_function(instructions="do", user_input="thing")

    assert len(recorded) == 1
    row = recorded[0]
    assert row["input_tokens"] == 12
    assert row["output_tokens"] == 34
    assert row["cache_creation_tokens"] == 56
    assert row["cache_read_tokens"] == 78


# =========================================================================
# AnthropicHelperSDK
# =========================================================================

@pytest.mark.asyncio
async def test_anthropic_helper_records_uncached_input_not_the_total(monkeypatch):
    """``input_tokens`` must be the FULL-RATE bucket alone.

    The provider reports uncached=100, write=20, read=30. Writing 150 into
    input_tokens (the previous behaviour, via the provider-neutral total)
    would price the cached 50 at the full rate.
    """
    import contextvars

    from xyz_agent_context.agent_framework.api_config import (
        AnthropicHelperConfig, ClaudeConfig, OpenAIConfig, set_user_config,
    )
    from xyz_agent_context.agent_framework.llm import anthropic_helper as ah_mod
    from xyz_agent_context.agent_framework.llm.anthropic_helper import (
        AnthropicHelperSDK,
    )

    class _Usage:
        input_tokens = 100
        cache_creation_input_tokens = 20
        cache_read_input_tokens = 30
        output_tokens = 7

    class _Block:
        type = "text"
        text = "hello"

    class _Msg:
        content = [_Block()]
        usage = _Usage()

    class _Stream:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get_final_message(self):
            return _Msg()

    class _Messages:
        def stream(self, **kwargs):
            return _Stream()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr(
        AnthropicHelperSDK, "_build_client", staticmethod(lambda: _Client())
    )

    recorded: list[dict] = []

    async def _fake_record_cost(**kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(ah_mod, "record_cost", _fake_record_cost)
    monkeypatch.setattr(
        ah_mod, "get_cost_context", lambda: ("agent_x", object())
    )

    def _install():
        set_user_config(
            ClaudeConfig(), OpenAIConfig(),
            anthropic_helper=AnthropicHelperConfig(api_key="k"),
        )
    contextvars.copy_context().run(_install)
    _install()

    sdk = AnthropicHelperSDK()
    await sdk.llm_function(instructions="do", user_input="thing")

    assert len(recorded) == 1
    row = recorded[0]
    assert row["input_tokens"] == 100, "must be uncached only, not the 150 total"
    assert row["cache_creation_tokens"] == 20
    assert row["cache_read_tokens"] == 30
    assert row["output_tokens"] == 7
