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
        "output_tokens": 6,
        "cache_creation_input_tokens": 4,
        "cache_read_input_tokens": 5,
        "total_tokens": 18,
    }


def test_normalize_anthropic_usage_handles_missing_usage():
    assert normalize_anthropic_usage(None) == {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "total_tokens": 0,
    }
