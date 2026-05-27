"""
@file_name: test_slack_text_sanitizer.py
@date: 2026-05-22
@description: Unit tests for the Slack mrkdwn defensive sanitizer.

The sanitizer fixes two real-world bugs we observed in agent replies
to CN users:

  1. GitHub markdown links ``[text](url)`` rendered as literal text in
     Slack (mrkdwn does not parse that syntax).
  2. Slack's auto-linkifier extending a bare URL across CJK
     punctuation, e.g. ``https://example.com，详细`` becoming a single
     broken URL.

These tests pin down both transformations plus their non-mangling
contract (idempotent, code-block-aware, existing-entity-aware).
"""

from __future__ import annotations

from xyz_agent_context.module.slack_module._slack_text_sanitizer import (
    sanitize_slack_mrkdwn,
)


# ── Markdown link → mrkdwn link ─────────────────────────────────────


def test_markdown_link_converts_to_mrkdwn() -> None:
    assert (
        sanitize_slack_mrkdwn("[click here](https://example.com)")
        == "<https://example.com|click here>"
    )


def test_markdown_link_with_cjk_text_converts() -> None:
    assert (
        sanitize_slack_mrkdwn("[点击](https://example.com)")
        == "<https://example.com|点击>"
    )


def test_multiple_markdown_links_each_convert() -> None:
    out = sanitize_slack_mrkdwn("[a](https://a.com) and [b](https://b.com)")
    assert out == "<https://a.com|a> and <https://b.com|b>"


def test_markdown_link_followed_by_cjk_punct_keeps_punct_outside() -> None:
    """A common CN pattern: [点击](url)，详细. The punctuation must
    stay outside the mrkdwn entity or it ends up in the visible link
    text."""
    out = sanitize_slack_mrkdwn("请[点击](https://example.com)，查看详细")
    assert out == "请<https://example.com|点击>，查看详细"


# ── Bare URL adjacent to CJK ────────────────────────────────────────


def test_bare_url_followed_by_cjk_punct_wrapped() -> None:
    """The reported bug: Slack auto-linker absorbs the `，` into the URL."""
    assert (
        sanitize_slack_mrkdwn("看 https://example.com，详细")
        == "看 <https://example.com>，详细"
    )


def test_bare_url_followed_by_cjk_char_wrapped() -> None:
    """No punctuation, just CJK adjacent: still absorbed by Slack."""
    assert (
        sanitize_slack_mrkdwn("看 https://example.com点击")
        == "看 <https://example.com>点击"
    )


def test_bare_url_followed_by_fullwidth_punct_wrapped() -> None:
    """Fullwidth period `。` and semicolon `；` are also non-ASCII."""
    assert (
        sanitize_slack_mrkdwn("访问 https://example.com。然后回来")
        == "访问 <https://example.com>。然后回来"
    )


def test_bare_url_with_path_followed_by_cjk_wrapped() -> None:
    """URL with path/query is wrapped at its true boundary."""
    out = sanitize_slack_mrkdwn(
        "见 https://example.com/path?q=1&x=2，详情"
    )
    assert out == "见 <https://example.com/path?q=1&x=2>，详情"


# ── Bare URL in pure-ASCII context: leave alone ─────────────────────


def test_bare_url_with_ascii_context_unchanged() -> None:
    """Slack's auto-linker handles ASCII boundaries correctly; we
    don't need to wrap and shouldn't churn the message."""
    text = "see https://example.com please"
    assert sanitize_slack_mrkdwn(text) == text


def test_bare_url_at_end_of_string_unchanged() -> None:
    text = "go to https://example.com"
    assert sanitize_slack_mrkdwn(text) == text


def test_bare_url_followed_by_newline_unchanged() -> None:
    text = "link:\nhttps://example.com\nthanks"
    assert sanitize_slack_mrkdwn(text) == text


# ── Already-formed Slack entities: don't double-wrap ────────────────


def test_already_wrapped_url_unchanged() -> None:
    assert (
        sanitize_slack_mrkdwn("<https://example.com>")
        == "<https://example.com>"
    )


def test_already_mrkdwn_link_unchanged() -> None:
    assert (
        sanitize_slack_mrkdwn("<https://example.com|click>")
        == "<https://example.com|click>"
    )


def test_user_mention_unchanged() -> None:
    assert sanitize_slack_mrkdwn("hi <@U123>") == "hi <@U123>"


def test_channel_mention_unchanged() -> None:
    assert (
        sanitize_slack_mrkdwn("see <#C123|general>")
        == "see <#C123|general>"
    )


def test_special_mention_unchanged() -> None:
    """``<!here>`` / ``<!channel>`` / ``<!subteam^...>`` patterns."""
    assert (
        sanitize_slack_mrkdwn("attention <!here>")
        == "attention <!here>"
    )


def test_already_wrapped_url_with_trailing_cjk_unchanged() -> None:
    """If the agent already wrapped the URL correctly, the trailing
    CJK isn't a problem and we must not introduce extra wrapping."""
    text = "见 <https://example.com>，详细"
    assert sanitize_slack_mrkdwn(text) == text


# ── Code blocks: passthrough ────────────────────────────────────────


def test_inline_code_url_not_touched() -> None:
    text = "`https://example.com，foo`"
    assert sanitize_slack_mrkdwn(text) == text


def test_triple_backtick_code_block_not_touched() -> None:
    text = "```\nhttps://example.com，foo\n[md](https://other.com)\n```"
    assert sanitize_slack_mrkdwn(text) == text


def test_code_block_then_buggy_text_outside_still_fixed() -> None:
    """Code stays untouched but text after the fence still gets fixed."""
    inp = (
        "Run this:\n"
        "```\n"
        "curl https://api.example.com，foo\n"
        "```\n"
        "Then see https://example.com，详细"
    )
    out = sanitize_slack_mrkdwn(inp)
    assert "curl https://api.example.com，foo" in out  # code preserved
    assert "Then see <https://example.com>，详细" in out  # text fixed


# ── Mixed / edge cases ──────────────────────────────────────────────


def test_markdown_link_then_bare_url_then_cjk() -> None:
    inp = "请[点击](https://a.com)，或者直接访问 https://b.com，谢谢"
    out = sanitize_slack_mrkdwn(inp)
    assert out == (
        "请<https://a.com|点击>，或者直接访问 <https://b.com>，谢谢"
    )


def test_empty_string_returns_empty() -> None:
    assert sanitize_slack_mrkdwn("") == ""


def test_plain_text_passthrough() -> None:
    text = "just some text 你好世界"
    assert sanitize_slack_mrkdwn(text) == text


def test_idempotent_on_buggy_input() -> None:
    """Running the sanitizer on its own output must be a no-op."""
    once = sanitize_slack_mrkdwn(
        "[点击](https://example.com)，详细 https://other.com。"
    )
    twice = sanitize_slack_mrkdwn(once)
    assert once == twice
