"""
@file_name: test_email_policy.py
@author: Bin Liang
@date: 2026-10-28
@description: The disposable-mailbox blocklist used by the signup routes.
"""
from __future__ import annotations

import pytest

from backend.routes._email_policy import DISPOSABLE_EMAIL_DOMAINS, is_blocked_email


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", raising=False)


def test_list_is_explicit_and_normalised():
    assert len(DISPOSABLE_EMAIL_DOMAINS) >= 50
    for d in DISPOSABLE_EMAIL_DOMAINS:
        assert d == d.strip().lower() and not d.startswith(".") and "@" not in d


@pytest.mark.parametrize("email,blocked", [
    ("a@mailinator.com", True),
    ("a@Mailinator.COM", True),
    ("a@x.y.mailinator.com", True),
    ("a@mailinator.com.", True),          # trailing root dot
    ("a@10minutemail.com", True),
    ("a@maxxspace.com", True),
    ("a@nimail.cn", True),
    ("a@cc.cd", True),
    ("a@guerrillamail.net", True),
    ("a@guerrillamail.org", True),
    ("a@guerrillamail.biz", True),
    ("a@grr.la", True),
    ("a@guerrillamailblock.com", True),
    ("a@10minutemail.net", True),
    ("a@temp-mail.io", True),
    # Long-lived alias / forwarding services front real, durable inboxes —
    # deliberately NOT listed.
    ("a@33mail.com", False),
    ("a@spamgourmet.com", False),
    ("a@burnermail.io", False),
    ("a@gmail.com", False),
    ("a@mailinator.com.evil.org", False),  # listed name as a LEFT label
    ("a@notyopmail.com", False),
    ("not-an-email", False),
])
def test_matching(email, blocked):
    assert is_blocked_email(email) is blocked


def test_env_extension(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", "corp-burner.io,, .Dots.Example.")
    assert is_blocked_email("a@corp-burner.io")
    assert is_blocked_email("a@mx.dots.example")
    assert not is_blocked_email("a@gmail.com")


def test_env_change_is_seen_on_the_next_call(monkeypatch):
    """The env is read per call: a later value replaces an earlier one
    in-process, it is never frozen at the first lookup."""
    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", "first-burner.io")
    assert is_blocked_email("a@first-burner.io")
    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", "second-burner.io")
    assert not is_blocked_email("a@first-burner.io")
    assert is_blocked_email("a@second-burner.io")
    monkeypatch.delenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS")
    assert not is_blocked_email("a@second-burner.io")
    assert is_blocked_email("a@mailinator.com")


def test_unchanged_env_reuses_the_merged_set(monkeypatch):
    """is_blocked_email runs on every NetMind login: an unchanged env must
    not rebuild the built-in + env union per call."""
    from backend.routes import _email_policy

    monkeypatch.setenv("NARRANEXUS_BLOCKED_EMAIL_DOMAINS", "cached-burner.io")
    _email_policy._blocked_domains.cache_clear()
    assert is_blocked_email("a@cached-burner.io")
    assert is_blocked_email("b@cached-burner.io")
    assert not is_blocked_email("c@example.com")
    info = _email_policy._blocked_domains.cache_info()
    assert (info.misses, info.hits) == (1, 2)
