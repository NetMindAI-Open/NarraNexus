"""
@file_name: test_telegram_skill_loader.py
@date: 2026-05-09
@description: Tests for TelegramSkillLoader — singleton accessor + lookup
behaviour against the bundled Bot-API method docs.

Why this file exists:
    The skill loader is the bridge between the agent's ``tg_skill(method)``
    MCP call and the markdown reference for that Bot API method. We
    verify that the loader actually indexes the bundled files, that
    multimodal methods (sendPhoto, sendDocument, sendVoice) are
    explicitly NOT shipped in Phase 4, and that unknown methods return
    a hint rather than crashing.
"""
from __future__ import annotations

from xyz_agent_context.module.telegram_module._telegram_skill_loader import (
    TelegramSkillLoader,
    get_skill_loader,
)


def test_get_skill_loader_returns_singleton():
    a = get_skill_loader()
    b = get_skill_loader()
    assert a is b
    assert isinstance(a, TelegramSkillLoader)


def test_list_methods_indexes_high_traffic_set():
    loader = get_skill_loader()
    methods = loader.list_methods()
    # Phase 4 ships ~25 high-traffic methods (text + admin + chat info)
    assert len(methods) >= 25
    assert methods == sorted(methods)


def test_get_returns_markdown_for_known_method():
    loader = get_skill_loader()
    body = loader.get("sendMessage")
    assert isinstance(body, str)
    assert body.strip()
    assert "sendMessage" in body


def test_get_unknown_multimodal_returns_hint():
    """sendPhoto is intentionally NOT bundled in Phase 4 — multimodal is
    deferred. The hint must mention valid methods + the upstream URL."""
    loader = get_skill_loader()
    out = loader.get("sendPhoto")
    assert "Unknown method" in out
    assert "core.telegram.org" in out


def test_get_unknown_method_returns_hint_with_categories():
    loader = get_skill_loader()
    out = loader.get("totally_fake_method")
    assert "Unknown method" in out
    assert "Categories" in out


def test_list_categories_includes_core_buckets():
    loader = get_skill_loader()
    categories = loader.list_categories()
    for expected in ("chat", "message", "webhook", "bot_info", "admin"):
        assert expected in categories
