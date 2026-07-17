//! Desktop NetMind ("Power") OAuth bridge.
//!
//! Why this exists: the web OAuth flow ([[useNetmindAuth]] `startOAuth`) opens
//! NetMind's `auth.html` in a browser popup and receives the result via
//! `window.opener.postMessage({type:'auth', code, state})`. That popup +
//! postMessage handshake does not work inside the packaged desktop webview
//! (WKWebView blocks the popup; there is no cross-window opener channel). Local
//! desktop never ran NetMind OAuth before Power login landed, so this is new.
//!
//! Approach (self-contained — needs NO NetMind-side change): open `auth.html`
//! in a Rust-created child webview and catch the result two independent ways so
//! the flow is robust:
//!
//!   1. **URL match (opener-independent).** After the provider authenticates,
//!      it redirects the webview back to NetMind's redirect_uri carrying
//!      `?code=&state=`. `on_navigation` matches any navigation whose query has
//!      BOTH params, extracts them, and synthesises the same `{type:'auth',...}`
//!      message — no reliance on `window.opener` being overridable.
//!   2. **Opener shim + sentinel (fallback).** An initialization script defines
//!      a `window.opener` whose `postMessage` navigates to a sentinel URL with
//!      the payload in the fragment; `on_navigation` catches that too.
//!
//! Delivery is via a buffered slot drained by `take_netmind_oauth_result`
//! (frontend polls it after starting OAuth) — mirroring the proven
//! `consume_pending_deep_link` path, so delivery never depends on a live Tauri
//! event listener (which needs `window.__TAURI__` and can silently no-op). A
//! redundant `emit` is also sent for any future live listener; harmless.
//!
//! No capability changes: the child webview only navigates (never invokes a
//! command or emits); buffering + emitting is Rust-privileged.

use tauri::{Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

use crate::state::AppState;

/// Label of the transient OAuth webview. Reused (closed + reopened) so repeated
/// clicks never stack windows.
const OAUTH_WINDOW_LABEL: &str = "netmind-oauth";

/// Host of the sentinel URL the injected shim navigates to. Never resolves —
/// `on_navigation` catches and cancels it. https (not a custom scheme) so it
/// stays on the normal navigation path on_navigation reliably sees.
const SENTINEL_HOST: &str = "nmoauth.callback";

/// Injected before every page load in the OAuth webview. NetMind's auth.html
/// ends the flow with `window.opener.postMessage({type:'auth', code, state})`.
/// A Rust-created top-level webview has a null opener, so we define one whose
/// postMessage forwards the payload to the sentinel URL. Best-effort: this is
/// only the fallback path (URL match above is primary), so failure is fine.
const OPENER_SHIM: &str = r#"
(function () {
  function forward(data) {
    try {
      var s = encodeURIComponent(JSON.stringify(data));
      window.location.href = 'https://nmoauth.callback/#' + s;
    } catch (e) { /* ignore */ }
  }
  var shim = { postMessage: function (d) { forward(d); } };
  try {
    Object.defineProperty(window, 'opener', {
      get: function () { return shim; },
      configurable: true,
    });
  } catch (e) {
    try { window.opener = shim; } catch (_) { /* ignore */ }
  }
})();
"#;

/// Buffer the payload (URI-encoded or plain JSON — the frontend decodes both),
/// fire a redundant live event, and close the OAuth window.
fn deliver(app: &tauri::AppHandle, payload: String) {
    {
        // Bind the lock result explicitly: rustc 1.80+ tightened temporary-scope
        // rules so `if let Ok(..) = state.pending.lock()` keeps the MutexGuard
        // temporary alive past the `state` binding (E0597). Same pattern lib.rs
        // uses for pending_deep_link.
        let state = app.state::<AppState>();
        let lock_result = state.pending_netmind_oauth.lock();
        if let Ok(mut slot) = lock_result {
            *slot = Some(payload.clone());
        }
    }
    if let Err(e) = app.emit("netmind-oauth-callback", payload) {
        log::warn!("emit netmind-oauth-callback failed: {e}");
    }
    if let Some(win) = app.get_webview_window(OAUTH_WINDOW_LABEL) {
        let _ = win.close();
    }
}

/// Open NetMind's OAuth page in a child webview and bridge its result back.
/// Frontend calls this only in Tauri (the browser build keeps window.open).
#[tauri::command]
pub async fn open_netmind_oauth(app: tauri::AppHandle, url: String) -> Result<(), String> {
    if let Some(existing) = app.get_webview_window(OAUTH_WINDOW_LABEL) {
        let _ = existing.close();
    }
    // Clear any stale buffered result from a previous attempt. Explicit lock
    // binding for the same rustc-1.80 temporary-scope reason as deliver().
    {
        let state = app.state::<AppState>();
        let lock_result = state.pending_netmind_oauth.lock();
        if let Ok(mut slot) = lock_result {
            *slot = None;
        }
    }

    let parsed = url
        .parse()
        .map_err(|e| format!("invalid oauth url {url:?}: {e}"))?;

    let app_for_nav = app.clone();
    WebviewWindowBuilder::new(&app, OAUTH_WINDOW_LABEL, WebviewUrl::External(parsed))
        .title("Sign in with NetMind")
        .inner_size(600.0, 720.0)
        .focused(true)
        .initialization_script(OPENER_SHIM)
        .on_navigation(move |target| {
            // Path 1 (fallback): sentinel from the opener shim. Fragment =
            // encodeURIComponent(JSON.stringify({type,code,state})).
            if target.host_str() == Some(SENTINEL_HOST) {
                let payload = target.fragment().unwrap_or("").to_string();
                deliver(&app_for_nav, payload);
                return false; // cancel the sentinel navigation
            }
            // Path 2 (primary, opener-independent): the provider redirected back
            // to NetMind's redirect_uri with ?code=&state=. Only the final
            // callback carries BOTH (the provider's auth URL has state but no
            // code), so requiring both avoids false positives mid-flow.
            let mut code: Option<String> = None;
            let mut state: Option<String> = None;
            for (k, v) in target.query_pairs() {
                match k.as_ref() {
                    "code" => code = Some(v.into_owned()),
                    "state" => state = Some(v.into_owned()),
                    _ => {}
                }
            }
            if let (Some(code), Some(state)) = (code, state) {
                let json =
                    serde_json::json!({ "type": "auth", "code": code, "state": state })
                        .to_string();
                deliver(&app_for_nav, json);
                return false; // cancel — we exchange code/state ourselves
            }
            true
        })
        .build()
        .map_err(|e| format!("failed to open oauth window: {e}"))?;

    Ok(())
}

/// Drain the buffered OAuth result (URI-encoded or plain JSON string), or None.
/// Frontend polls this after `open_netmind_oauth`. `take` clears the slot so a
/// result is consumed exactly once.
#[tauri::command]
pub async fn take_netmind_oauth_result(
    state: tauri::State<'_, AppState>,
) -> Result<Option<String>, String> {
    let mut slot = state
        .pending_netmind_oauth
        .lock()
        .map_err(|e| format!("pending_netmind_oauth mutex poisoned: {e}"))?;
    Ok(slot.take())
}
