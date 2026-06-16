// file_download.rs — save a backend file to the OS Downloads folder
//
// Why this exists
// ---------------
// Two download surfaces are broken on local/desktop surfaces:
//
// 1. Tauri DMG: the webview origin is `https://tauri.localhost` (HTTPS) while
//    the backend serves on `http://localhost:8000` (HTTP). WKWebView blocks
//    HTTP navigations initiated from an HTTPS document as mixed content — so
//    `<a href download>` silently does nothing. Additionally, the `download`
//    attribute is ignored for cross-origin URLs in all modern browsers.
//
// 2. Local browser (`bash run.sh`, Vite :5173 → backend :8000): the endpoint
//    is cross-origin so the `download` attribute is silently ignored (browser
//    navigates instead of saving). Workspace files also require `X-User-Id` /
//    `Authorization` headers that `<a>` elements cannot attach.
//
// Rust-originated HTTP is not subject to WKWebView's mixed-content rules.
// This command fetches the file bytes via reqwest (optionally with auth
// headers), saves them to the OS Downloads directory, and returns the saved
// path so the frontend can surface it.
//
// Security
// --------
// Only URLs whose host resolves to a loopback address on the backend port
// (8000) are accepted — the same SSRF guard strategy as artifact_fetch.rs.
// Two path prefixes are allowed:
//   /api/public/artifacts/  — token-based public artifact files
//   /api/agents/            — workspace files (JWT / X-User-Id authed)
// Anything else is rejected.

use std::collections::HashMap;
use std::path::PathBuf;
use std::time::Duration;
use url::Url;

const ALLOWED_HOSTS: &[&str] = &["localhost", "127.0.0.1", "::1"];
const ALLOWED_PORTS: &[u16] = &[8000];
const ALLOWED_PATH_PREFIXES: &[&str] = &["/api/public/artifacts/", "/api/agents/"];

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
    let path = parsed.path();
    let allowed = ALLOWED_PATH_PREFIXES.iter().any(|prefix| path.starts_with(prefix));
    if !allowed {
        return Err(format!("path prefix not allowed: {path}"));
    }
    Ok(parsed)
}

/// Sanitise `filename` to a basename: strip any directory component, replace
/// path separator chars, and limit length to avoid OS limits.
fn safe_basename(filename: &str) -> String {
    // Take only the last component after any '/' or '\'.
    let base = filename
        .rsplit(|c| c == '/' || c == '\\')
        .next()
        .unwrap_or(filename);
    // Replace remaining illegal chars (NUL, colon, etc.).
    let sanitised: String = base
        .chars()
        .map(|c| match c {
            '\0' | ':' | '*' | '?' | '"' | '<' | '>' | '|' => '_',
            c => c,
        })
        .collect();
    // Trim trailing dots/spaces (Windows compat) and cap to 200 chars.
    let trimmed = sanitised.trim_end_matches(['.', ' ']).to_string();
    if trimmed.is_empty() {
        "download".to_string()
    } else {
        trimmed.chars().take(200).collect()
    }
}

/// Resolve a non-conflicting output path inside `dir` for `filename`.
/// If `dir/filename` already exists, tries `dir/stem (1).ext`,
/// `dir/stem (2).ext`, … up to 99 before giving up.
fn resolve_output_path(dir: &PathBuf, filename: &str) -> Result<PathBuf, String> {
    let candidate = dir.join(filename);
    if !candidate.exists() {
        return Ok(candidate);
    }
    // Split stem and extension.
    let (stem, ext) = match filename.rfind('.') {
        Some(idx) if idx > 0 => (&filename[..idx], &filename[idx..]),
        _ => (filename, ""),
    };
    for n in 1u32..=99 {
        let new_name = format!("{stem} ({n}){ext}");
        let path = dir.join(&new_name);
        if !path.exists() {
            return Ok(path);
        }
    }
    Err(format!("could not find a free filename for '{filename}' after 99 attempts"))
}

/// Save a backend file to the OS Downloads folder.
///
/// `url`     — must pass the loopback + port 8000 + allowed-prefix validation.
/// `filename` — suggested filename; sanitised to a bare basename before use.
/// `headers` — optional auth headers (e.g. `X-User-Id`, `Authorization`).
///             Artifact public URLs carry an access token in the query string
///             and pass `None`; workspace URLs need the user's session headers.
///
/// Returns the absolute path of the saved file on success.
#[tauri::command]
pub async fn download_file_via_backend(
    url: String,
    filename: String,
    headers: Option<HashMap<String, String>>,
) -> Result<String, String> {
    let parsed = validate(&url)?;

    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(30))
        .build()
        .map_err(|e| format!("client build failed: {e}"))?;

    let mut req = client.get(parsed.as_str());
    if let Some(hdrs) = headers {
        for (k, v) in &hdrs {
            let name = reqwest::header::HeaderName::from_bytes(k.as_bytes())
                .map_err(|e| format!("invalid header name '{k}': {e}"))?;
            let value = reqwest::header::HeaderValue::from_str(v)
                .map_err(|e| format!("invalid header value for '{k}': {e}"))?;
            req = req.header(name, value);
        }
    }

    let resp = req
        .send()
        .await
        .map_err(|e| format!("request failed: {e}"))?;

    if !resp.status().is_success() {
        let status = resp.status().as_u16();
        return Err(format!("download failed: HTTP {status}"));
    }

    let bytes = resp
        .bytes()
        .await
        .map_err(|e| format!("body read failed: {e}"))?;

    // Resolve save directory: Downloads, or home as fallback.
    let save_dir = dirs::download_dir()
        .or_else(dirs::home_dir)
        .ok_or_else(|| "could not determine downloads directory".to_string())?;

    let base = safe_basename(&filename);
    let output_path = resolve_output_path(&save_dir, &base)?;

    std::fs::write(&output_path, &bytes)
        .map_err(|e| format!("write failed: {e}"))?;

    output_path
        .to_str()
        .map(|s| s.to_string())
        .ok_or_else(|| "saved path contains non-UTF-8 characters".to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn validate_accepts_artifact_loopback_url() {
        validate("http://localhost:8000/api/public/artifacts/raw/tok/index.html").unwrap();
        validate("http://127.0.0.1:8000/api/public/artifacts/raw/tok/").unwrap();
    }

    #[test]
    fn validate_accepts_agents_files_loopback_url() {
        validate("http://localhost:8000/api/agents/agt_abc123/files/raw?path=report.csv").unwrap();
        validate("http://127.0.0.1:8000/api/agents/x/files/raw?path=a.txt").unwrap();
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
        assert!(validate("http://localhost:8000/api/auth/login").is_err());
    }

    #[test]
    fn validate_rejects_file_scheme() {
        assert!(validate("file:///etc/passwd").is_err());
    }

    #[test]
    fn safe_basename_strips_directory_components() {
        assert_eq!(safe_basename("/tmp/secret/file.txt"), "file.txt");
        assert_eq!(safe_basename("..\\..\\etc\\passwd"), "passwd");
    }

    #[test]
    fn safe_basename_sanitises_illegal_chars() {
        assert_eq!(safe_basename("my:file?.txt"), "my_file_.txt");
    }

    #[test]
    fn safe_basename_handles_empty() {
        assert_eq!(safe_basename(""), "download");
    }
}
