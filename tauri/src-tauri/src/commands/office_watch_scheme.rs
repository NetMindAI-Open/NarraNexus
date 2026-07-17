// office_watch_scheme.rs — the `officewatch://` custom URI scheme for the
// desktop live Office preview.
//
// Why this exists
// ---------------
// The live preview needs the officecli-watch page loaded in an iframe. In the
// dmg the webview origin is `https://tauri.localhost` while the backend serves
// `http://localhost:8000` — WKWebView blocks that HTTP iframe as active mixed
// content (same P0 as artifacts). Static artifacts dodge this with a base64
// blob (see artifact_fetch.rs), but a blob is a STATIC snapshot and the watch
// page pulls its own sub-resources (assets/fonts/katex) and an SSE endpoint —
// a blob can carry none of that.
//
// So instead we serve the whole watch page under a CUSTOM scheme. A custom
// scheme is NOT mixed content, so the webview loads it; the page's own
// root-relative sub-requests resolve back under `officewatch://` (via the
// backend-injected <base>) and land here too, so every asset flows through
// Rust. Rust originates the HTTP to the backend, which WKWebView does not
// police.
//
// Live refresh: Tauri's custom-scheme responder answers a request ONCE and
// can't hold an SSE stream, so we short-circuit the page's `/events` request
// with an empty body (no live push on desktop). Updates instead come from the
// frontend's mtime poll → iframe reload (OfficeWatchViewer) — the watch page's
// GET always renders the current document.
//
// Security
// --------
// Only the token-authed public proxy path is proxied; anything else is 403.
// The token in the path is the auth (minted by the backend `open` endpoint),
// exactly as in the browser. Requests only ever go to loopback :8000.

use std::borrow::Cow;
use std::time::Duration;

use tauri::http::{Request, Response};

/// Loopback backend the desktop sidecar serves on.
const BACKEND: &str = "http://localhost:8000";
/// Only the token-authed public office-watch proxy may be reached.
const ALLOWED_PREFIX: &str = "/api/public/office-watch-proxy/";

/// Handle one `officewatch://` request by proxying it to the local backend.
///
/// `officewatch://localhost/api/public/office-watch-proxy/{token}/{port}/...`
/// maps 1:1 to `http://localhost:8000/api/public/office-watch-proxy/...`.
pub async fn handle(request: Request<Vec<u8>>) -> Response<Cow<'static, [u8]>> {
    let uri = request.uri();
    let path = uri.path();

    // SSRF guard, part 1: reject dot-segments BEFORE the prefix check. `path`
    // is later concatenated verbatim into the backend URL, so a webview that
    // does not normalize `..` for a custom scheme could otherwise let
    // `/api/public/office-watch-proxy/../../secret` satisfy `starts_with` yet
    // resolve to an arbitrary backend route. Splitting on '/' catches `..` in
    // any segment without rejecting legitimate names that merely contain dots.
    if path.split('/').any(|seg| seg == "..") {
        return build(403, "text/plain", b"forbidden".to_vec());
    }

    // SSRF guard, part 2: refuse anything but the token-authed public proxy path.
    if !path.starts_with(ALLOWED_PREFIX) {
        return build(403, "text/plain", b"forbidden".to_vec());
    }

    // Desktop can't hold an SSE stream through a single-respond custom scheme,
    // so answer the watch page's EventSource('/events') with an empty stream.
    // Live updates arrive via the frontend's mtime-poll → iframe reload.
    if path.ends_with("/events") {
        return build(200, "text/event-stream", Vec::new());
    }

    let mut url = format!("{BACKEND}{path}");
    if let Some(query) = uri.query() {
        url.push('?');
        url.push_str(query);
    }

    let client = match reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
    {
        Ok(c) => c,
        Err(e) => return build(502, "text/plain", format!("client: {e}").into_bytes()),
    };

    match client.get(&url).send().await {
        Ok(resp) => {
            let status = resp.status().as_u16();
            let content_type = resp
                .headers()
                .get(reqwest::header::CONTENT_TYPE)
                .and_then(|v| v.to_str().ok())
                .unwrap_or("application/octet-stream")
                .to_string();
            let body = resp.bytes().await.map(|b| b.to_vec()).unwrap_or_default();
            build(status, &content_type, body)
        }
        Err(e) => build(502, "text/plain", format!("upstream: {e}").into_bytes()),
    }
}

/// Build a response. The sandboxed iframe has an opaque origin, so its requests
/// to `officewatch://` are cross-origin — mirror the browser proxy and allow
/// them with a permissive CORS header (auth is the token in the path).
fn build(status: u16, content_type: &str, body: Vec<u8>) -> Response<Cow<'static, [u8]>> {
    // Guard the one caller-controlled header: an upstream Content-Type carrying
    // bytes illegal in a header value would make `body()` return Err, and the
    // old `.expect()` would panic. Inside an async custom-scheme handler tokio
    // silently drops the panicking task, so `responder.respond` is never called
    // and the webview hangs with no error. Pre-validate the value and fall back
    // to a safe static one so every header is known-good and `.expect` on a
    // fully-validated builder can never fire.
    let ct = reqwest::header::HeaderValue::from_str(content_type)
        .unwrap_or_else(|_| reqwest::header::HeaderValue::from_static("application/octet-stream"));
    Response::builder()
        .status(status)
        .header("Content-Type", ct)
        .header("Access-Control-Allow-Origin", "*")
        .header("Cache-Control", "no-store")
        .body(Cow::Owned(body))
        .expect("response with pre-validated headers is always buildable")
}
