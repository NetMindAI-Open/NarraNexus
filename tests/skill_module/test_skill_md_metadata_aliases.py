"""
@file_name: test_skill_md_metadata_aliases.py
@author: Bin Liang
@date: 2026-09-03
@description: SKILL.md runtime requirements are read from metadata.openclaw / clawdbot / clawdis alike.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.skill_module.skill_module import (
    SKILL_METADATA_KEYS,
    SkillModule,
    _skill_runtime_requires,
)

_SKILL_MD = """---
name: weather
description: Weather lookups
metadata:
  {key}:
    requires:
      env: [WEATHER_KEY]
      bins: [curl]
---
# Weather
Use `curl` with `$WEATHER_KEY`.
"""


def test_the_alias_vocabulary_is_pinned_and_shared_with_the_migration_scanner():
    # Literal, not derived: dropping an alias must fail here, not silently shrink the parametrization below.
    assert SKILL_METADATA_KEYS == ("openclaw", "clawdbot", "clawdis", "moltbot")
    from narranexus.platform.migration.detector import _SIGNALS
    from narranexus.contracts.openclaw import OPENCLAW_ALIASES

    assert SKILL_METADATA_KEYS is OPENCLAW_ALIASES
    assert _SIGNALS["openclaw"]["home_dirs"] == [f".{n}" for n in OPENCLAW_ALIASES]
    assert set(f"{n}.json" for n in OPENCLAW_ALIASES) <= set(_SIGNALS["openclaw"]["strong"])


@pytest.mark.parametrize("key", SKILL_METADATA_KEYS)
def test_every_alias_yields_the_same_requirements(tmp_path, key):
    skill_dir = tmp_path / "weather"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(_SKILL_MD.format(key=key), encoding="utf-8")
    module = SkillModule("agent_test", "user_test", None)
    info = module._parse_skill_md(skill_dir / "SKILL.md")
    assert info.requires_env == ["WEATHER_KEY"]
    assert info.requires_bins == ["curl"]


def test_newest_key_wins_and_missing_or_malformed_blocks_are_empty():
    assert _skill_runtime_requires(
        {"openclaw": {"requires": {"env": ["NEW"]}}, "clawdbot": {"requires": {"env": ["OLD"]}}}
    ) == {"env": ["NEW"]}
    assert _skill_runtime_requires({"clawdis": {"requires": {"bins": ["x"]}}}) == {"bins": ["x"]}
    assert _skill_runtime_requires({}) == {}
    assert _skill_runtime_requires({"openclaw": "not a dict"}) == {}
    assert _skill_runtime_requires({"openclaw": {"requires": "bad"}}) == {}
