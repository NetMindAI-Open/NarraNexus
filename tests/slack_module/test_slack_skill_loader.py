"""
@file_name: test_slack_skill_loader.py
@date: 2026-05-08
@description: Tests for SlackSkillLoader — singleton accessor + lookup
behaviour against the bundled OpenAPI-derived skill files.

Why this file exists:
    The skill loader is the bridge between an Agent's `slack_skill(method)`
    MCP call and the markdown reference for that Web API method. We
    verify that the loader actually indexes the bundled files and that
    unknown methods return a hint rather than crashing.
"""
from __future__ import annotations

from xyz_agent_context.module.slack_module import _slack_skill_loader as loader_mod
from xyz_agent_context.module.slack_module._slack_skill_loader import (
    SlackSkillLoader,
    get_skill_loader,
)


def test_get_skill_loader_returns_singleton():
    a = get_skill_loader()
    b = get_skill_loader()
    assert a is b
    assert isinstance(a, SlackSkillLoader)


def test_list_methods_indexes_bundled_skills():
    loader = get_skill_loader()
    methods = loader.list_methods()
    # Slack OpenAPI yields ~250 methods; we shipped at least 100.
    assert len(methods) >= 100
    # Sorted contract
    assert methods == sorted(methods)


def test_list_methods_filters_by_prefix():
    loader = get_skill_loader()
    chat_methods = loader.list_methods("chat.")
    assert chat_methods, "expected at least one chat.* method"
    assert all(m.startswith("chat.") for m in chat_methods)


def test_list_categories_includes_core_buckets():
    loader = get_skill_loader()
    categories = loader.list_categories()
    for expected in ("chat", "users", "conversations"):
        assert expected in categories


def test_get_returns_markdown_for_known_method():
    loader = get_skill_loader()
    body = loader.get("chat.postMessage")
    assert isinstance(body, str)
    assert body.strip()
    # Skill docs should include something method-identifying
    assert "chat.postMessage" in body or "chat_postMessage" in body or "postMessage" in body


def test_get_unknown_method_returns_same_category_hint():
    loader = get_skill_loader()
    out = loader.get("chat.does_not_exist")
    assert "Unknown method" in out
    # Should suggest known chat.* methods
    assert "chat." in out


def test_get_unknown_method_with_unknown_category_returns_category_hint():
    loader = get_skill_loader()
    out = loader.get("totally.fake.method")
    assert "Unknown method" in out
    # Should fall back to listing available categories
    assert "categor" in out.lower()


def test_underscore_prefixed_files_excluded_from_index():
    loader = get_skill_loader()
    methods = loader.list_methods()
    assert not any(m.startswith("_") for m in methods)
