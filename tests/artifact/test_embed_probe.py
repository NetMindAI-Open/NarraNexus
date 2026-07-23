"""
@file_name: test_embed_probe.py
@author: Bin Liang
@date: 2026-07-22
@description: Tests for embed_probe — the pure classifier and the async probe.

`classify_embeddability` is tested exhaustively with no network. `probe_url`
is tested against an httpx MockTransport so redirects, header reads, and
failure degradation are covered deterministically.
"""
from __future__ import annotations

import httpx
import pytest

from xyz_agent_context.artifact._artifact_impl.embed_probe import (
    classify_embeddability,
    probe_url,
)


# ── pure classifier ──────────────────────────────────────────────────────────


def test_no_headers_is_iframe():
    v = classify_embeddability(final_url="https://ok.example/", headers={}, our_scheme="https")
    assert v.recommended == "iframe"
    assert v.reason == "no-blocking-headers"
    assert v.effective_mode == "iframe"


@pytest.mark.parametrize("xfo", ["DENY", "deny", "SAMEORIGIN", "SameOrigin", "  deny  "])
def test_x_frame_options_forces_stream(xfo):
    v = classify_embeddability(
        final_url="https://x.example/", headers={"x-frame-options": xfo}, our_scheme="https"
    )
    assert v.recommended == "stream"
    assert v.reason == "x-frame-options"


def test_x_frame_options_allow_from_streams():
    # The deprecated ALLOW-FROM permits exactly one embedder that is never us.
    v = classify_embeddability(
        final_url="https://x.example/",
        headers={"x-frame-options": "ALLOW-FROM https://trusted.example"},
        our_scheme="https",
    )
    assert v.recommended == "stream"
    assert v.reason == "x-frame-options"


def test_csp_frame_ancestors_none_streams():
    v = classify_embeddability(
        final_url="https://x.example/",
        headers={"content-security-policy": "default-src 'self'; frame-ancestors 'none'"},
        our_scheme="https",
    )
    assert v.recommended == "stream"
    assert v.reason == "csp-frame-ancestors"


def test_csp_frame_ancestors_self_streams():
    v = classify_embeddability(
        final_url="https://x.example/",
        headers={"content-security-policy": "frame-ancestors 'self'"},
        our_scheme="https",
    )
    assert v.recommended == "stream"


def test_csp_frame_ancestors_wildcard_allows_iframe():
    # A wildcard frame-ancestors permits any embedder — we can iframe it.
    v = classify_embeddability(
        final_url="https://x.example/",
        headers={"content-security-policy": "frame-ancestors *"},
        our_scheme="https",
    )
    assert v.recommended == "iframe"


def test_csp_without_frame_ancestors_ignored():
    # A CSP that doesn't mention frame-ancestors does not block embedding.
    v = classify_embeddability(
        final_url="https://x.example/",
        headers={"content-security-policy": "default-src 'self'"},
        our_scheme="https",
    )
    assert v.recommended == "iframe"


def test_http_target_in_https_app_is_mixed_content():
    v = classify_embeddability(
        final_url="http://insecure.example/", headers={}, our_scheme="https"
    )
    assert v.recommended == "stream"
    assert v.reason == "mixed-content"


def test_http_target_in_http_app_is_fine():
    # Local dev (http) embedding an http target — no mixed content.
    v = classify_embeddability(
        final_url="http://insecure.example/", headers={}, our_scheme="http"
    )
    assert v.recommended == "iframe"


# ── async probe (httpx MockTransport) ────────────────────────────────────────


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


@pytest.mark.asyncio
async def test_probe_reads_final_headers():
    def handler(request):
        return httpx.Response(200, headers={"x-frame-options": "DENY"})

    async with _client(handler) as c:
        v = await probe_url(
            "https://blocks.example/", our_scheme="https", client=c,
            resolver=lambda h, p: _ok(),
        )
    assert v.recommended == "stream"
    assert v.reason == "x-frame-options"


@pytest.mark.asyncio
async def test_probe_follows_redirects_to_final_hop():
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://dest.example/end"})
        return httpx.Response(200, headers={})  # final: embeddable

    async with _client(handler) as c:
        v = await probe_url(
            "https://redir.example/start", our_scheme="https", client=c,
            resolver=lambda h, p: _ok(),
        )
    assert v.recommended == "iframe"


@pytest.mark.asyncio
async def test_probe_network_error_degrades_to_iframe():
    def handler(request):
        raise httpx.ConnectError("boom")

    async with _client(handler) as c:
        v = await probe_url(
            "https://down.example/", our_scheme="https", client=c,
            resolver=lambda h, p: _ok(),
        )
    assert v.recommended == "iframe"
    assert v.probe_status == "failed"
    assert v.reason == "probe-failed"


@pytest.mark.asyncio
async def test_probe_redirect_to_internal_degrades_not_crashes():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    async with _client(handler) as c:
        v = await probe_url(
            "https://evil.example/", our_scheme="https", client=c,
        )
    # The redirect hop is SSRF-blocked; probe degrades to iframe, no exception.
    assert v.probe_status == "failed"
    assert v.recommended == "iframe"


async def _ok():
    return ["93.184.216.34"]
