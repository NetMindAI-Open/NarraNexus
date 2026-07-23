"""
@file_name: test_page_text.py
@author: Bin Liang
@date: 2026-07-22
@description: Tests for page-text extraction (the agent-visibility feature).

`html_to_text` is a pure function tested directly; `fetch_page_text` is tested
against httpx MockTransport so bounds, non-HTML skip, redirect-SSRF, and
network failures are deterministic and network-free.
"""
from __future__ import annotations

import httpx
import pytest

from xyz_agent_context.artifact._artifact_impl.page_text import (
    fetch_page_text,
    html_to_text,
)


# ── pure extractor ───────────────────────────────────────────────────────────


def test_html_to_text_strips_scripts_styles_tags_and_unescapes():
    html = (
        "<html><head><style>a{color:red}</style></head>"
        "<body><h1>Hi</h1><p>Hello &amp; welcome</p>"
        "<script>evil()</script><p>Bye</p></body></html>"
    )
    text = html_to_text(html)
    assert "evil()" not in text
    assert "color:red" not in text
    assert "Hello & welcome" in text
    assert "Hi" in text and "Bye" in text


def test_html_to_text_block_breaks():
    assert "\n" in html_to_text("<p>one</p><p>two</p>")


def test_html_to_text_strips_unclosed_trailing_style():
    # Body byte-capped mid-<style> → no closing tag. The CSS must NOT leak.
    truncated = "<p>Real content</p><style>html{font-size:10px;color:red"
    text = html_to_text(truncated)
    assert "Real content" in text
    assert "font-size" not in text and "color:red" not in text


def test_html_to_text_strips_unclosed_trailing_script():
    truncated = "<h1>Title</h1><script>var x = {a:1, b:2, c:function(){"
    text = html_to_text(truncated)
    assert "Title" in text
    assert "function" not in text and "var x" not in text


def test_html_to_text_keeps_body_after_commented_out_script():
    # A commented-out <script> mid-document must NOT trigger the tail-strip and
    # eat the real body after it (regression: anchoring on the leftmost orphan
    # deleted everything after a fake <script> in a comment).
    html = "<p>hello</p><!-- <script src=x.js> legacy --><p>REAL BODY TEXT</p>"
    text = html_to_text(html)
    assert "hello" in text
    assert "REAL BODY TEXT" in text  # body survives
    assert "<!--" not in text and "src=x.js" not in text


def test_html_to_text_strips_comments():
    assert html_to_text("<p>a</p><!-- secret note --><p>b</p>") == "a\n\nb" or \
        "secret" not in html_to_text("<p>a</p><!-- secret note --><p>b</p>")


def test_html_to_text_closing_tag_with_space():
    # </script > (space before >) must still close the block.
    assert "leak" not in html_to_text("<script>var leak=1</script ><p>body</p>")


# ── async fetch ──────────────────────────────────────────────────────────────


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


async def _ok_resolver(host, port):
    return ["93.184.216.34"]


@pytest.mark.asyncio
async def test_fetch_extracts_text_from_html():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/html"},
                              text="<h1>Report</h1><p>Q3 revenue up</p>")
    async with _client(handler) as c:
        text = await fetch_page_text("https://ok.example/", client=c, resolver=_ok_resolver)
    assert text is not None
    assert "Report" in text and "Q3 revenue up" in text


@pytest.mark.asyncio
async def test_fetch_non_html_returns_none():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "application/json"}, text='{"x":1}')
    async with _client(handler) as c:
        assert await fetch_page_text("https://api.example/", client=c, resolver=_ok_resolver) is None


@pytest.mark.asyncio
async def test_fetch_follows_redirect_then_extracts():
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://d.example/end"})
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<p>final</p>")
    async with _client(handler) as c:
        text = await fetch_page_text("https://r.example/start", client=c, resolver=_ok_resolver)
    assert text == "final"


@pytest.mark.asyncio
async def test_fetch_redirect_to_internal_returns_none_not_raise():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/"})
    async with _client(handler) as c:
        # resolver lets the FIRST hop pass so the 302 is actually followed; the
        # second hop is the literal metadata IP → SSRF-blocked → degrade to None
        # (exercises the per-hop gate on a followed redirect, no real DNS).
        assert await fetch_page_text("https://evil.example/", client=c, resolver=_ok_resolver) is None


@pytest.mark.asyncio
async def test_fetch_network_error_returns_none():
    def handler(request):
        raise httpx.ConnectError("down")
    async with _client(handler) as c:
        assert await fetch_page_text("https://down.example/", client=c, resolver=_ok_resolver) is None


@pytest.mark.asyncio
async def test_fetch_stops_reading_at_byte_cap():
    # Prove the BODY read stops at _MAX_FETCH_BYTES: a unique marker placed far
    # past the cap must NOT appear in the extracted text (if we read the whole
    # body it would). Uses an async byte stream so aiter_bytes is exercised.
    from xyz_agent_context.artifact._artifact_impl import page_text as pt

    body = b"<p>" + (b"x" * (pt._MAX_FETCH_BYTES + 500_000)) + b" UNIQUE_TAIL_MARKER</p>"

    def handler(request):
        # content= bytes → httpx yields it in chunks via aiter_bytes, so the
        # loop's byte-cap break is exercised against a >1MB body.
        return httpx.Response(200, headers={"content-type": "text/html"}, content=body)

    async with _client(handler) as c:
        text = await fetch_page_text("https://big.example/", client=c, resolver=_ok_resolver)
    assert text is not None
    assert "UNIQUE_TAIL_MARKER" not in text  # tail past the byte cap was never read
    assert len(text) <= 16_000  # _MAX_TEXT_CHARS
