// artifact_fetch.rs — proxy artifact bytes from local backend through Rust
//
// Why this exists
// ---------------
// In the dmg the Tauri webview origin is `https://tauri.localhost` (HTTPS)
// while the backend serves on `http://localhost:8000` (HTTP). WKWebView
// classifies any HTTP subresource loaded from an HTTPS document as "active
// mixed content" and blocks it silently — both iframe loads AND `fetch()`
// from JS. That made every artifact tab render as a white frame (P0
// reported 2026-05-27).
//
// HTTP requests *originated by Rust* are not subject to the WKWebView's
// mixed-content rules. This command fetches the artifact bytes via reqwest
// and ships them back to the frontend as base64 over the IPC channel. The
// frontend reconstructs a `Blob` and uses a `blob:` URL as the iframe src,
// which is treated as same-origin to the parent and therefore allowed.
//
// Security
// --------
// Only URLs whose host:port resolves to a loopback address on the backend
// port are accepted — refuse anything else so a hostile artifact link
// can't turn this into a generic SSRF tool that exfiltrates intranet
// content from the user's machine.

use base64::Engine;
use serde::Serialize;
use std::time::Duration;
use url::Url;

#[derive(Serialize)]
pub struct ArtifactBytes {
    pub status: u16,
    pub content_type: String,
    pub bytes_base64: String,
}

const ALLOWED_HOSTS: &[&str] = &["localhost", "127.0.0.1", "::1"];
const ALLOWED_PORTS: &[u16] = &[8000];
const ALLOWED_PATH_PREFIX: &str = "/api/public/artifacts/";

fn validate(url: &str) -> Result<Url, String> {
    let parsed = Url::parse(url).map_err(|e| format!("invalid url: {e}"))?;
    if parsed.scheme() != "http" && parsed.scheme() != "https" {
        return Err(format!("unsupported scheme: {}", parsed.scheme()));
    }
    let host = parsed.host_str().ok_or_else(|| "missing host".to_string())?;
    if !ALLOWED_HOSTS.contains(&host) {
        return Err(format!("host not allowed: {host}"));
    }
    let port = parsed.port().unwrap_or(match parsed.scheme() {
        "http" => 80,
        "https" => 443,
        _ => 0,
    });
    if !ALLOWED_PORTS.contains(&port) {
        return Err(format!("port not allowed: {port}"));
    }
    if !parsed.path().starts_with(ALLOWED_PATH_PREFIX) {
        return Err(format!("path prefix not allowed: {}", parsed.path()));
    }
    Ok(parsed)
}

#[tauri::command]
pub async fn fetch_artifact_via_backend(url: String) -> Result<ArtifactBytes, String> {
    let parsed = validate(&url)?;

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("client build failed: {e}"))?;

    let resp = client
        .get(parsed.as_str())
        .send()
        .await
        .map_err(|e| format!("request failed: {e}"))?;

    let status = resp.status().as_u16();
    let content_type = resp
        .headers()
        .get(reqwest::header::CONTENT_TYPE)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("application/octet-stream")
        .to_string();

    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("body read failed: {e}"))?;

    let bytes_base64 = base64::engine::general_purpose::STANDARD.encode(&bytes);

    Ok(ArtifactBytes {
        status,
        content_type,
        bytes_base64,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_accepts_localhost_artifact() {
        validate("http://localhost:8000/api/public/artifacts/raw/tok/").unwrap();
        validate("http://127.0.0.1:8000/api/public/artifacts/raw/tok/index.html").unwrap();
    }

    #[test]
    fn validate_rejects_external_host() {
        assert!(validate("http://evil.example.com:8000/api/public/artifacts/").is_err());
    }

    #[test]
    fn validate_rejects_wrong_port() {
        assert!(validate("http://localhost:9999/api/public/artifacts/").is_err());
    }

    #[test]
    fn validate_rejects_other_path() {
        assert!(validate("http://localhost:8000/api/agents/x/secret").is_err());
    }

    #[test]
    fn validate_rejects_file_scheme() {
        assert!(validate("file:///etc/passwd").is_err());
    }
}
