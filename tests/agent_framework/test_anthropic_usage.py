"""
@file_name: test_anthropic_usage.py
@date: 2026-07-28
@description: Regression tests for provider-neutral Anthropic usage totals.
"""

from types import SimpleNamespace

from xyz_agent_context.agent_framework.anthropic_usage import (
    normalize_anthropic_usage,
)


def test_normalize_anthropic_usage_includes_cache_buckets_from_object():
    usage = SimpleNamespace(
        input_tokens=11,
        cache_creation_input_tokens=7,
        cache_read_input_tokens=13,
        output_tokens=5,
    )

    assert normalize_anthropic_usage(usage) == {
        "input_tokens": 31,
        "uncached_input_tokens": 11,
        "output_tokens": 5,
        "cache_creation_input_tokens": 7,
        "cache_read_input_tokens": 13,
        "total_tokens": 36,
    }


def test_normalize_anthropic_usage_accepts_cli_dict_shape():
    assert normalize_anthropic_usage(
        {
            "input_tokens": 3,
            "cache_creation_input_tokens": 4,
            "cache_read_input_tokens": 5,
            "output_tokens": 6,
        }
    ) == {
        "input_tokens": 12,
        "uncached_input_tokens": 3,
        "output_tokens": 6,
        "cache_creation_input_tokens": 4,
        "cache_read_input_tokens": 5,
        "total_tokens": 18,
    }


def test_normalize_anthropic_usage_handles_missing_usage():
    assert normalize_anthropic_usage(None) == {
        "input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
    }


def test_uncached_bucket_is_exposed_not_left_to_callers_to_subtract():
    """The full-rate bucket must be readable directly.

    Callers that bill the three buckets separately (cost_tracker via the
    helper SDKs) would otherwise each rederive it as `input - cw - cr`; one
    sign slip there is a silent 10x error, since cache reads bill at 0.1x.
    """
    usage = normalize_anthropic_usage(
        {"input_tokens": 100, "cache_creation_input_tokens": 20,
         "cache_read_input_tokens": 30, "output_tokens": 1}
    )
    assert usage["uncached_input_tokens"] == 100
    assert usage["input_tokens"] == 150
    assert (
        usage["input_tokens"]
        == usage["uncached_input_tokens"]
        + usage["cache_creation_input_tokens"]
        + usage["cache_read_input_tokens"]
    )
