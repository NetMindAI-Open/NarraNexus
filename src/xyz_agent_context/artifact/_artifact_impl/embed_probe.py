"""
@file_name: embed_probe.py
@author: Bin Liang
@date: 2026-07-22
@description: Decide whether a URL can be <iframe>-embedded, or needs streaming.

Two parts, deliberately split so the decision logic is testable without a
network:

- `classify_embeddability(...)` — a PURE function: response headers + our
  serving scheme in, an `EmbedVerdict` out. Exhaustively unit-testable.
- `probe_url(...)` — the async orchestration: fetch the URL (following
  redirects manually so every hop passes the SSRF gate), then hand the final
  hop's headers to the pure classifier. Never raises for a normal failure —
  a failed probe degrades to an optimistic `iframe` verdict (a blank iframe
  is a cheap, self-correcting failure the user can flip). SSRF rejection of
  ANY hop (including the first) also degrades to iframe here; the *initial*
  URL's hard reject is done separately by the caller (`open_url`) before it
  ever calls this, so the browser-fetched iframe is the only thing left.

Why "probe failed → iframe" and not "→ stream": the failure of an iframe
embed is visible and instantly fixable (user clicks the mode toggle),
whereas defaulting to stream would silently burn a server-side browser and
the user would never discover the site was embeddable. Default to the cheap
failure.
"""

from __future__ import annotations

from typing import Mapping, Optional
from urllib.parse import urlparse

import httpx
from loguru import logger

from xyz_agent_context.schema.artifact_schema import EmbedVerdict
from xyz_agent_context.utils.safe_http import RedirectLimitError, safe_stream_get
from xyz_agent_context.utils.url_safety import Resolver, UnsafeUrlError

_PROBE_TIMEOUT_S = 5.0


def _frame_ancestors_blocks(directive_value: str) -> bool:
    """Given the value of a CSP `frame-ancestors` directive, decide whether it
    blocks us. A wildcard `*` allows any embedder → does not block. Anything
    else (`'none'`, `'self'`, an explicit host list we're almost never in) is
    treated as blocking — we conservatively stream rather than risk a broken
    embed."""
    tokens = directive_value.split()
    return "*" not in tokens


def _extract_frame_ancestors(csp: str) -> Optional[str]:
    """Pull the `frame-ancestors` directive value out of a CSP header, or None
    if the directive is absent."""
    for directive in csp.split(";"):
        directive = directive.strip()
        if directive.lower().startswith("frame-ancestors"):
            return directive[len("frame-ancestors"):].strip()
    return None


def classify_embeddability(
    *,
    final_url: str,
    headers: Mapping[str, str],
    our_scheme: str,
) -> EmbedVerdict:
    """Pure decision: can the browser embed `final_url` in our iframe?

    Args:
        final_url: the URL after following redirects (its scheme drives the
            mixed-content check).
        headers: the final response's headers (case-insensitive lookup
            expected — httpx.Headers satisfies this).
        our_scheme: the scheme WE are served on ("https" in cloud, "http" in
            local dev) — an http target inside an https app is blocked as
            mixed content.

    Returns:
        An EmbedVerdict with `recommended` + `reason`, `probe_status="ok"`.
    """
    xfo = headers.get("x-frame-options")
    if xfo:
        xfo_val = xfo.strip().lower()
        # DENY / SAMEORIGIN block us outright; the deprecated ALLOW-FROM <origin>
        # permits exactly one embedder that is never us — all block embedding.
        if xfo_val in ("deny", "sameorigin") or xfo_val.startswith("allow-from"):
            return EmbedVerdict(recommended="stream", reason="x-frame-options", probe_status="ok")

    csp = headers.get("content-security-policy")
    if csp:
        fa = _extract_frame_ancestors(csp)
        if fa is not None and _frame_ancestors_blocks(fa):
            return EmbedVerdict(recommended="stream", reason="csp-frame-ancestors", probe_status="ok")

    target_scheme = urlparse(final_url).scheme
    if our_scheme == "https" and target_scheme == "http":
        return EmbedVerdict(recommended="stream", reason="mixed-content", probe_status="ok")

    return EmbedVerdict(recommended="iframe", reason="no-blocking-headers", probe_status="ok")


async def probe_url(
    url: str,
    *,
    our_scheme: str,
    resolver: Optional[Resolver] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> EmbedVerdict:
    """Fetch `url` server-side and classify its embeddability.

    Follows redirects manually (≤_MAX_REDIRECTS) so every hop is re-validated
    against the SSRF gate — a public URL that 302s to an internal host is
    caught mid-chain. A failed probe returns an optimistic `iframe` verdict
    with `probe_status="failed"`; the caller has already hard-rejected an
    unsafe *initial* URL, so SSRF on a later hop just degrades the probe.

    Redirect-following + per-hop SSRF gate live in the shared
    `utils.safe_http.safe_stream_get`; we only read the final hop's headers
    (never its body). `client` is injectable for tests; `resolver` is passed
    through to the SSRF gate.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=False, timeout=_PROBE_TIMEOUT_S)
    try:
        async with safe_stream_get(client, url, resolver=resolver) as resp:
            return classify_embeddability(
                final_url=str(resp.url), headers=resp.headers, our_scheme=our_scheme
            )
    except RedirectLimitError:
        logger.warning("embed probe hit redirect limit for {}", url)
        return EmbedVerdict(recommended="iframe", reason="too-many-redirects", probe_status="failed")
    except UnsafeUrlError as e:
        # A hop pointed inside the network. Degrade the probe (the browser, not
        # us, will fetch the iframe); don't crash tab creation.
        logger.warning("embed probe SSRF-blocked a hop for {}: {}", url, e)
        return EmbedVerdict(recommended="iframe", reason="probe-failed", probe_status="failed")
    except Exception as e:  # noqa: BLE001 — any network failure degrades, never crashes
        logger.info("embed probe failed for {}: {}", url, e)
        return EmbedVerdict(recommended="iframe", reason="probe-failed", probe_status="failed")
    finally:
        if owns_client:
            await client.aclose()
