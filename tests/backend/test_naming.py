"""
@file_name: test_naming.py
@author: Bin Liang
@date: 2026-08-19
@description: Unit tests for the shared random-name generator extracted from
              the Arena onboarding module (three-group Nintendo-style names).
"""

import random

import pytest

from backend.naming import (
    BASE_NAME_COMBINATIONS,
    GROUP_CREATURE,
    GROUP_FORCE,
    GROUP_TEMPERAMENT,
    NameExhausted,
    generate_name,
    generate_unique_name,
)


def test_generate_name_is_three_groups():
    name = generate_name(random.Random(42))
    a, b, c = name.split("_")
    assert a in GROUP_TEMPERAMENT
    assert b in GROUP_FORCE
    assert c in GROUP_CREATURE


def test_generate_name_without_rng_still_valid():
    a, b, c = generate_name().split("_")
    assert a in GROUP_TEMPERAMENT and b in GROUP_FORCE and c in GROUP_CREATURE


def test_combinations_count():
    assert BASE_NAME_COMBINATIONS == 24 * 24 * 24


def test_generate_unique_name_returns_free_name():
    # The first roll of Random(7) is taken; the generator must reroll past it.
    taken = {generate_name(random.Random(7))}
    name = generate_unique_name(lambda n: n in taken, rng=random.Random(7))
    assert name not in taken


def test_generate_unique_name_suffixes_after_rerolls():
    # Every base name is "taken"; only a numeric-suffixed candidate is free.
    name = generate_unique_name(
        lambda n: "_" not in n[-3:],  # frees names ending in _NN
        rng=random.Random(3),
        reroll_attempts=2,
        suffix_attempts=5,
    )
    assert name[-3] == "_" and name[-2:].isdigit()


def test_generate_unique_name_exhaustion():
    with pytest.raises(NameExhausted):
        generate_unique_name(
            lambda n: True,
            rng=random.Random(1),
            reroll_attempts=2,
            suffix_attempts=2,
        )
