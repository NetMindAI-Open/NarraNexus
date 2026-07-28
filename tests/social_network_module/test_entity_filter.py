"""
@file_name: test_entity_filter.py
@author: Bin Liang
@date: 2026-07-23
@description: Tests for the meaningless-entity filter in the social network
extraction pipeline (bug tracker: "Social Network entity 图无意义条目").

Covers the deterministic guard `is_meaningful_entity`:
- generic placeholder names (user/assistant/团队/大家/...) are rejected
- bare platform/system IDs, pure digits, uuid/hex blobs are rejected
- absurdly long "names" are rejected
- low LLM confidence is rejected; the default confidence passes
- ordinary human/agent names (latin + CJK) pass
and that `extract_mentioned_entities` applies the guard to LLM output.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from xyz_agent_context.module.social_network_module._entity_updater import (
    BatchExtractionOutput,
    ExtractedEntity,
    extract_mentioned_entities,
    is_meaningful_entity,
)


def _e(name: str, **kw) -> ExtractedEntity:
    return ExtractedEntity(name=name, **kw)


class TestIsMeaningfulEntity:
    def test_ordinary_names_pass(self):
        assert is_meaningful_entity(_e("Alice"))
        assert is_meaningful_entity(_e("张伟"))
        assert is_meaningful_entity(_e("alpha4"))
        assert is_meaningful_entity(_e("Data Squad", entity_type="group"))

    def test_generic_placeholders_rejected(self):
        for name in ("user", "User", "assistant", "the agent", "admin", "bot",
                     "someone", "everyone", "team", "用户", "助手", "大家",
                     "团队", "某人", "unknown", "N/A"):
            assert not is_meaningful_entity(_e(name)), name

    def test_bare_system_ids_rejected(self):
        for name in ("ou_eef37b72b4f25ebe7b72e83a5499e1be", "agent_7ce1e3120f46",
                     "usr_a1b2c3d4", "1df321656338429097459e33b0aae971",
                     "123456789", "550e8400-e29b-41d4-a716-446655440000"):
            assert not is_meaningful_entity(_e(name)), name

    def test_overlong_names_rejected(self):
        assert not is_meaningful_entity(_e("x" * 81))

    def test_low_confidence_rejected_default_passes(self):
        assert not is_meaningful_entity(_e("Alice", confidence=0.3))
        assert is_meaningful_entity(_e("Alice", confidence=0.9))
        # Field omitted → default must pass (older helper outputs).
        assert is_meaningful_entity(_e("Alice"))


@pytest.mark.asyncio
async def test_extract_applies_meaningfulness_filter():
    """LLM junk (generic names, bare IDs, low confidence) never reaches the
    graph; real names survive."""
    llm_output = BatchExtractionOutput(entities=[
        _e("Alice"),
        _e("users"),
        _e("ou_deadbeefdeadbeefdeadbeef"),
        _e("Bob", confidence=0.1),
    ])

    fake_result = type("R", (), {"final_output": llm_output})()
    fake_sdk = type("S", (), {"llm_function": AsyncMock(return_value=fake_result)})()

    with patch(
        "xyz_agent_context.module.social_network_module._entity_updater.get_helper_sdk",
        return_value=fake_sdk,
    ):
        out = await extract_mentioned_entities("hi", "hello", primary_entity_name="Carol")

    assert [e.name for e in out] == ["Alice"]
