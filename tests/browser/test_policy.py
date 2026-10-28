"""
@file_name: test_policy.py
@author:
@date: 2026-09-22
@description: Tests for the per-origin arbitrary-script permission model.

The one decided capability is ``full_cdp_access``; ordinary website browsing
is outside this policy model. Origin matching, inheritance and legacy-document
loading are pinned here.
"""
from __future__ import annotations

import pytest

from narranexus.platform.browser._browser_impl.policy import (
    BrowserPolicy,
    OriginPolicy,
    decide,
    origin_of,
)

CDP = "full_cdp_access"


def policy(**kw) -> BrowserPolicy:
    return BrowserPolicy(
        default_origin_policy=kw.pop("default", OriginPolicy()),
        origins=kw.pop("origins", {}),
    )


def verdict(p: BrowserPolicy, url: str) -> str:
    return decide(p, url=url, capability=CDP).verdict


# ── origin_of ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://github.com/features", "https://github.com"),
        ("https://github.com:443/x", "https://github.com"),
        ("http://example.com:8080/a/b", "http://example.com:8080"),
        ("https://GitHub.COM/X", "https://github.com"),
        ("https://sub.github.com/", "https://sub.github.com"),
    ],
)
def test_origin_of_normalises(url, expected):
    assert origin_of(url) == expected


@pytest.mark.parametrize(
    "url", ["file:///etc/passwd", "javascript:alert(1)", "data:text/html,x", "", "not a url"]
)
def test_origin_of_rejects_non_http_schemes(url):
    """file: and javascript: are how a navigation becomes a local-file read or
    a script injection; they never resolve to an origin a rule can name."""
    assert origin_of(url) is None


# ── defaults are safe ────────────────────────────────────────────────────────


@pytest.mark.parametrize("document", [None, {}, {"default_origin_policy": {}, "origins": {}}])
def test_scripts_are_denied_by_default_for_new_and_existing_agents(document):
    """Arbitrary scripts are the whole browser; nothing allows them implicitly."""
    p = BrowserPolicy() if document is None else BrowserPolicy.from_dict(document)
    d = decide(p, url="https://github.com/", capability=CDP)
    assert d.verdict == "deny"
    assert d.matched == "default"
    assert d.reason == "builtin-default"


def test_non_http_url_is_denied_even_when_allowed_everywhere():
    d = decide(policy(default=OriginPolicy(full_cdp_access="allow")), url="file:///etc/passwd", capability=CDP)
    assert d.verdict == "deny"
    assert d.reason == "unsupported-scheme"


# ── explicit origin beats default ────────────────────────────────────────────


def test_exact_origin_overrides_default():
    p = policy(origins={"https://github.com": OriginPolicy(full_cdp_access="allow")})
    assert verdict(p, "https://github.com/x") == "allow"
    assert verdict(p, "https://other.com/x") == "deny"


def test_deny_on_the_origin_is_not_overridden_by_an_allowing_default():
    p = policy(
        default=OriginPolicy(full_cdp_access="allow"),
        origins={"https://bad.example": OriginPolicy(full_cdp_access="deny")},
    )
    assert verdict(p, "https://bad.example/") == "deny"
    assert verdict(p, "https://fine.example/") == "allow"


def test_origin_match_is_scheme_sensitive():
    """An http:// page is not the https:// origin the owner allowed."""
    p = policy(origins={"https://bank.example": OriginPolicy(full_cdp_access="allow")})
    assert verdict(p, "http://bank.example/") == "deny"


def test_wildcard_host_matches_subdomains():
    p = policy(origins={"https://*.github.com": OriginPolicy(full_cdp_access="allow")})
    assert verdict(p, "https://gist.github.com/x") == "allow"


def test_wildcard_does_not_match_the_bare_domain():
    """`*.github.com` and `github.com` are different rules; conflating them
    is how a permission silently widens."""
    p = policy(origins={"https://*.github.com": OriginPolicy(full_cdp_access="allow")})
    assert verdict(p, "https://github.com/") == "deny"


def test_wildcard_does_not_match_a_lookalike_suffix():
    """`evil-github.com` must not match `*.github.com`."""
    p = policy(origins={"https://*.github.com": OriginPolicy(full_cdp_access="allow")})
    assert verdict(p, "https://evil-github.com/") == "deny"


def test_exact_match_wins_over_wildcard():
    p = policy(origins={
        "https://*.github.com": OriginPolicy(full_cdp_access="allow"),
        "https://gist.github.com": OriginPolicy(full_cdp_access="deny"),
    })
    assert verdict(p, "https://gist.github.com/") == "deny"


def test_an_origin_entry_without_a_verdict_inherits_the_default():
    p = policy(
        default=OriginPolicy(full_cdp_access="allow"),
        origins={"https://x.example": OriginPolicy()},
    )
    d = decide(p, url="https://x.example/", capability=CDP)
    assert d.verdict == "allow"
    assert d.matched == "https://x.example"


# ── serialisation ────────────────────────────────────────────────────────────


def test_policy_round_trips_through_dict():
    p = policy(
        default=OriginPolicy(full_cdp_access="deny"),
        origins={"https://x.example": OriginPolicy(full_cdp_access="allow")},
    )
    again = BrowserPolicy.from_dict(p.to_dict())
    assert again.to_dict() == p.to_dict()
    assert verdict(again, "https://x.example/") == "allow"


def test_documents_from_earlier_builds_still_load():
    """Earlier builds also stored file-transfer verdicts, session grants,
    approval receipts and a history-access flag. None of them is enforced any
    more; a stored row must load with its script rules intact, not fail."""
    p = BrowserPolicy.from_dict({
        "allow_history_access": True,
        "default_origin_policy": {"downloads": "ask", "uploads": "ask", "future_thing": "x"},
        "origins": {
            "https://a.example": {"full_cdp_access": "allow", "downloads": "deny"},
            "https://only-files.example": {"downloads": "allow"},
        },
        "grants": [["https://b.example", "downloads", "thread:th"]],
        "denials": [],
        "approval_receipts": ["appr_1"],
    })
    assert verdict(p, "https://a.example/") == "allow"
    assert verdict(p, "https://b.example/") == "deny"
    # An origin that carried only retired verdicts is not kept as an empty rule.
    assert set(p.origins) == {"https://a.example"}
    assert p.to_dict() == {
        "default_origin_policy": {},
        "origins": {"https://a.example": {"full_cdp_access": "allow"}},
    }


@pytest.mark.parametrize("value", ["maybe", "ask"])
def test_from_dict_rejects_an_invalid_verdict_value(value):
    """A typo'd verdict must fail loudly at load, not silently become a default."""
    with pytest.raises(ValueError, match=value):
        BrowserPolicy.from_dict({"default_origin_policy": {"full_cdp_access": value}})
