"""
@file_name: test_backoff.py
@author:
@date: 2026-07-13
@description: Unit tests for the shared exponential-backoff formula.
"""

import pytest

from xyz_agent_context.utils.backoff import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_BACKOFF_CAP_SECONDS,
    compute_cooldown_seconds,
)


@pytest.mark.parametrize(
    "n,expected",
    [
        (1, 60),
        (2, 120),
        (3, 240),
        (4, 480),
        (5, 960),
        (6, 1920),
        (7, 3600),   # 3840 clamped to the 1h cap
        (8, 3600),   # plateaus at the cap
        (50, 3600),  # far past the cap
    ],
)
def test_backoff_schedule_and_cap(n, expected):
    assert compute_cooldown_seconds(n) == expected


def test_backoff_floors_at_one():
    # 0 / negative counts are treated as the first failure, never < base.
    assert compute_cooldown_seconds(0) == DEFAULT_BACKOFF_BASE_SECONDS
    assert compute_cooldown_seconds(-5) == DEFAULT_BACKOFF_BASE_SECONDS


def test_backoff_custom_base_and_cap():
    assert compute_cooldown_seconds(1, base=10, cap=100) == 10
    assert compute_cooldown_seconds(2, base=10, cap=100) == 20
    assert compute_cooldown_seconds(4, base=10, cap=100) == 80
    assert compute_cooldown_seconds(5, base=10, cap=100) == 100  # 160 clamped
    assert DEFAULT_BACKOFF_CAP_SECONDS == 3600
