//! `plugin://<plugin id>/<asset path>` — serves a user plugin's built frontend
//! assets from the plugin home so the desktop webview can `import()` them
//! without mixed-content or path exposure.
//!
//! Only files under `<plugin home>/<id>/frontend/dist/` are reachable: the
//! id must look like `<publisher>.<name>`, every path segment is checked
//! (no `..`, no empty segments, no absolute paths), and the resolved file
//! must still be inside the dist directory after canonicalisation (symlink
//! escapes are refused). The plugin home follows the kernel's rule:
//! `NARRANEXUS_PLUGIN_HOME` when set, else `$HOME/.narranexus/plugins`
//! (see `narranexus/kernel/plugins/paths.py`).

use std::borrow::Cow;
use std::path::{Component, Path, PathBuf};

use tauri::http::{Request, Response};

fn plugin_home() -> Option<PathBuf> {
    if let Ok(v) = std::env::var("NARRANEXUS_PLUGIN_HOME") {
        if !v.trim().is_empty() {
            return Some(PathBuf::from(v.trim()));
        }
    }
    std::env::var("HOME")
        .ok()
        .map(|h| Path::new(&h).join(".narranexus").join("plugins"))
}

fn valid_plugin_id(id: &str) -> bool {
    id.contains('.')
        && !id.starts_with('.')
        && !id.ends_with('.')
        && id
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-'))
}

fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).unwrap_or("") {
        "js" | "mjs" => "text/javascript",
        "css" => "text/css",
        "json" => "application/json",
        "map" => "application/json",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "jpg" | "jpeg" => "image/jpeg",
        "gif" => "image/gif",
        "webp" => "image/webp",
        "woff" => "font/woff",
        "woff2" => "font/woff2",
        "wasm" => "application/wasm",
        "html" => "text/html",
        _ => "application/octet-stream",
    }
}

/// Resolve `plugin://<id>/<rel>` to a file inside the plugin's dist dir, or `None` when refused.
pub fn resolve(home: &Path, host: &str, rel: &str) -> Option<PathBuf> {
    if !valid_plugin_id(host) {
        return None;
    }
    let rel = rel.trim_start_matches('/');
    if rel.is_empty() {
        return None;
    }
    let rel_path = Path::new(rel);
    if rel_path
        .components()
        .any(|c| !matches!(c, Component::Normal(_)))
    {
        return None;
    }
    let dist = home.join(host).join("frontend").join("dist");
    let candidate = dist.join(rel_path);
    let dist_canon = dist.canonicalize().ok()?;
    let file_canon = candidate.canonicalize().ok()?;
    if !file_canon.starts_with(&dist_canon) || !file_canon.is_file() {
        return None;
    }
    Some(file_canon)
}

pub async fn handle(request: Request<Vec<u8>>) -> Response<Cow<'static, [u8]>> {
    let uri = request.uri();
    let host = uri.host().unwrap_or("");
    let path = uri.path();
    let Some(home) = plugin_home() else {
        return build(500, "text/plain", b"plugin home unavailable".to_vec());
    };
    match resolve(&home, host, path) {
        Some(file) => match std::fs::read(&file) {
            Ok(bytes) => build(200, content_type(&file), bytes),
            Err(_) => build(404, "text/plain", b"not found".to_vec()),
        },
        None => build(403, "text/plain", b"forbidden".to_vec()),
    }
}

fn build(status: u16, content_type: &str, body: Vec<u8>) -> Response<Cow<'static, [u8]>> {
    Response::builder()
        .status(status)
        .header("Content-Type", content_type)
        .header("Cache-Control", "no-store")
        .header("Access-Control-Allow-Origin", "*")
        .body(Cow::Owned(body))
        .expect("response with static headers is always buildable")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn home() -> tempfile::TempDir {
        let dir = tempfile::tempdir().expect("tempdir");
        let dist = dir.path().join("acme.weather").join("frontend").join("dist");
        std::fs::create_dir_all(&dist).unwrap();
        std::fs::write(dist.join("plugin.js"), b"export const plugin = 1;").unwrap();
        std::fs::write(dir.path().join("acme.weather").join("narranexus-plugin.json"), b"{}").unwrap();
        dir
    }

    #[test]
    fn serves_only_dist_files() {
        let h = home();
        assert!(resolve(h.path(), "acme.weather", "/plugin.js").is_some());
        assert!(resolve(h.path(), "acme.weather", "/missing.js").is_none());
        assert!(resolve(h.path(), "acme.weather", "/../narranexus-plugin.json").is_none());
        assert!(resolve(h.path(), "acme.weather", "/../../acme.weather/frontend/dist/plugin.js").is_none());
        assert!(resolve(h.path(), "nodejs", "/plugin.js").is_none());
        assert!(resolve(h.path(), "ACME.weather", "/plugin.js").is_none());
        assert!(resolve(h.path(), "acme.weather", "").is_none());
    }

    #[test]
    fn content_types() {
        assert_eq!(content_type(Path::new("a.js")), "text/javascript");
        assert_eq!(content_type(Path::new("a.css")), "text/css");
        assert_eq!(content_type(Path::new("a.bin")), "application/octet-stream");
    }
}
