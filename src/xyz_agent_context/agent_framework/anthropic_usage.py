"""
@file_name: anthropic_usage.py
@author: NarraNexus
@date: 2026-07-28
@description: Normalize Anthropic usage into provider-neutral token totals.
"""

from collections.abc import Mapping
from typing import Any


def _usage_value(usage: Any, field: str) -> int:
    if isinstance(usage, Mapping):
        value = usage.get(field, 0)
    else:
        value = getattr(usage, field, 0)
    return int(value or 0)


def normalize_anthropic_usage(usage: Any) -> dict[str, int]:
    """Return complete Anthropic input and output usage.

    Anthropic reports uncached input, cache writes, and cache reads as
    separate top-level fields. Provider-neutral input usage includes all
    three buckets. OpenAI differs because its input total already includes
    cached input and exposes the cached count only as a breakdown.
    """
    uncached_input_tokens = _usage_value(usage, "input_tokens")
    cache_creation_input_tokens = _usage_value(
        usage, "cache_creation_input_tokens"
    )
    cache_read_input_tokens = _usage_value(usage, "cache_read_input_tokens")
    output_tokens = _usage_value(usage, "output_tokens")
    input_tokens = (
        uncached_input_tokens
        + cache_creation_input_tokens
        + cache_read_input_tokens
    )

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
