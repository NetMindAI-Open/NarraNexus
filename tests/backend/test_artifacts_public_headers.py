"""
Regression tests for the artifact public-raw response headers — making sure
the iframe-embedding fix (2026-05-27) doesn't get accidentally reverted.

Symptom this guards against: the dmg's artifact panel went white because
the response carried `X-Frame-Options: SAMEORIGIN` + `Cross-Origin-
Resource-Policy: same-origin`, both of which made the browser refuse to
render the iframe (the parent webview is `https://tauri.localhost` while
the artifact bytes come from `http://localhost:8000` — different origin).
Surgical replacement: `frame-ancestors {parent_origin}` in CSP, with
the parent origin detected from Referer/Origin per request.
"""
from __future__ import annotations

from backend.routes.artifacts_public import (
    SAFE_HEADERS,
    _csp_for_html,
    _frame_ancestors,
    _non_html_csp,
)


def test_safe_headers_does_not_set_x_frame_options():
    # X-Frame-Options is the legacy all-or-nothing header that has no way
    # to express "the dmg's tauri.localhost is OK even though we serve
    # from localhost:8000". Setting it back will re-introduce the white-
    # screen P0.
    assert "X-Frame-Options" not in SAFE_HEADERS
    # Case-insensitive check (HTTP headers are case-insensitive).
    assert "x-frame-options" not in {k.lower() for k in SAFE_HEADERS}


def test_safe_headers_corp_is_cross_origin():
    # `same-origin` made the browser discard the response. Auth lives in
    # the HMAC token in the URL path, not in origin — anyone with the
    # token can read, so `cross-origin` is correct.
    assert SAFE_HEADERS["Cross-Origin-Resource-Policy"] == "cross-origin"


def test_csp_html_includes_frame_ancestors_with_parent_origin():
    origin = "https://tauri.localhost"
    csp = _csp_for_html(origin)
    assert f"frame-ancestors {_frame_ancestors(origin)}" in csp
    # And the script/style/etc. directives still pin to the request's host
    # (existing behaviour we're not regressing).
    assert f"script-src {origin}" in csp
    assert f"style-src {origin}" in csp
    assert "default-src 'none'" in csp


def test_csp_non_html_includes_frame_ancestors():
    # Every non-HTML kind (chart / csv / markdown / image / pdf) also gets
    # framed in the dmg webview, so they need the same allowance.
    for kind in [
        "application/vnd.echarts+json",
        "text/csv",
        "text/markdown",
        "image/png",
        "image/jpeg",
        "application/pdf",
    ]:
        csp = _non_html_csp(kind, "https://tauri.localhost")
        assert "frame-ancestors" in csp
        assert "https://tauri.localhost" in csp
        assert "http://127.0.0.1:*" in csp, (
            f"missing frame-ancestors in CSP for {kind!r}: {csp!r}"
        )


def test_csp_non_html_unknown_kind_falls_back_to_default_src_none_plus_frame_ancestors():
    # Unknown kinds shouldn't bypass the frame-ancestors allowance.
    csp = _non_html_csp("application/octet-stream", "https://tauri.localhost")
    assert "default-src 'none'" in csp
    assert "frame-ancestors" in csp
    assert "https://tauri.localhost" in csp


def test_csp_html_for_cloud_origin():
    # Sanity: works with a cloud-style origin too, not just tauri.localhost.
    csp = _csp_for_html("https://agent.narra.nexus")
    assert "frame-ancestors https://agent.narra.nexus" in csp
    assert "https://tauri.localhost" in csp
    assert "script-src https://agent.narra.nexus" in csp
