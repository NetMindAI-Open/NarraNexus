---
code_file: frontend/src/lib/download.ts
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 两个平台的错误契约统一：一律 throw，成功返回保存路径

签名 `Promise<void>` → `Promise<string | null>`，Tauri 分支不再自己 catch。

**改前两个平台的契约是不一致的**，而且这个不一致本身就是 bug：

| 分支 | 失败时 |
|---|---|
| Tauri | 内部 catch → `window.alert` → `return`（正常 resolve） |
| 浏览器 | `throw` |

wry 不渲染 `window.alert`，所以桌面端失败**完全无声**；更隐蔽的是，因为它 resolve 而
不是 reject，**调用方自己写的 `.catch()` 在桌面端是死代码**，而同一个 `.catch()` 在浏览器
里工作正常。[[FileUpload]] 正是这种情况。而 [[ArtifactDownloadMenu]] 压根没有 `.catch()`
—— 浏览器里是未处理的 promise rejection（静默），桌面端是不可见 alert，**两个平台都没有
可用的错误路径**。

**返回值不是顺手加的**：桌面端保存到 `~/Downloads` 且**没有浏览器的下载条**，
`savedPath` 是用户得知文件去哪儿的唯一途径。所以这不是「丢了一条错误提示」，而是在唯一
需要它的平台上丢掉了成功反馈 —— 下载成功看起来跟什么都没发生一样。浏览器分支返回 null
（文件已交给下载管理器，没有路径可报）。

报告职责移交给调用方（两个，都是组件，都用 `useConfirm().alert`）。这也顺带修掉了原先
那两条**裸英文、未 i18n** 的字符串（`Download failed:` / `Saved to:`），现在是
`common.downloadFailed` / `common.savedTo`，10 语言齐平。

契约由 `lib/__tests__/download.test.ts` 钉住：两个平台各自「失败必 throw 且不碰
window.alert」、桌面端返回路径、`isTauri()` 中途翻转时返回 null 而不编造路径。

# download.ts — Cross-surface file download utility

## Why it exists

Two download surfaces were silently broken when using the standard
`<a href download>` approach:

1. **Tauri DMG** — the webview origin is `https://tauri.localhost`
   (HTTPS) while the backend serves on `http://localhost:8000` (HTTP).
   WKWebView classifies HTTP navigations initiated from an HTTPS
   document as "active mixed content" and blocks them silently. Even
   if the request got through, the `download` attribute is ignored for
   cross-origin URLs in all modern browsers.

2. **Local browser** (`bash run.sh`, Vite `:5173` → backend `:8000`)
   — cross-origin, so the `download` attribute is silently ignored
   (browser navigates instead of saving). Workspace files additionally
   require `X-User-Id` / `Authorization` headers that an `<a>` element
   cannot attach, causing a 401.

The fix is a single `downloadFile({ url, filename, authHeaders? })`
function that picks the correct strategy per runtime surface:

- **Tauri path**: delegates to `downloadFileViaTauri()` from
  `lib/tauri.ts`, which invokes the Rust `download_file_via_backend`
  command. Rust-originated HTTP is immune to WKWebView's mixed-content
  blocker. The command saves the file to `~/Downloads` and returns the
  absolute path, which `downloadFile` surfaces via `window.alert`.
- **Browser path**: issues `fetch(url, { headers: authHeaders })`,
  converts the response to a Blob, creates an object URL, appends a
  programmatic `<a download>` to the body, clicks it, and immediately
  revokes the object URL. The `fetch()` call carries any auth headers
  and lands the bytes in memory first, bypassing the cross-origin
  `<a download>` restriction.

## This file does not do

- Chart image export (PNG/JPEG from ECharts canvas) — that uses a
  `data:` URL and a programmatic `<a download>` directly in
  `ArtifactDownloadMenu`. That path does not hit backend endpoints and
  is not cross-origin, so no helper is needed.
- Auth header generation — callers pass `api.getAuthHeaders()` for
  workspace files; artifact raw URLs are public (token in query string)
  so `authHeaders` is omitted.

## Upstream / Downstream

- **Called by**: `ArtifactDownloadMenu.tsx` (for the "Download
  original" entry; artifact URLs are public, so `authHeaders` is
  omitted) and `FileUpload.tsx` (for per-file workspace Download
  buttons; `api.getAuthHeaders()` is passed as `authHeaders` because
  workspace endpoints are auth-gated).
- **Depends on**: `lib/tauri.ts` (`isTauri`, `downloadFileViaTauri`).
  The browser fetch path has no external dependencies.

## Design decisions

- **Single entry point, surface-detected internally.** Callers do not
  branch on `isTauri()` themselves — `downloadFile()` handles it. This
  keeps surface-specific logic in one place and makes callers uniform.
- **`authHeaders` is optional at the interface level.** Artifact URLs
  encode access tokens in the query string; forcing callers to always
  pass headers would be misleading. Passing `undefined` naturally
  omits the `headers` option from `fetch()`.
- **Tauri errors surfaced via `window.alert`.** The Tauri path catches
  errors from the Rust command and alerts the user. This is intentional
  for now: the download button is a low-stakes UI control and the
  simpler alert avoids needing a toast/notification system in this
  utility.

## Gotchas

- **`isTauri()` race at mount**: if `isTauri()` returns `true` but the
  Tauri IPC channel is not yet attached, `downloadFileViaTauri` returns
  `null`. `downloadFile` handles this by returning silently. The
  "Download" button will not be visible in that state, so this is a
  benign edge case.
- **Browser `fetch` requires CORS.** On the local browser surface the
  backend must include `Access-Control-Allow-Origin` headers for the
  workspace and artifact endpoints. This is already configured; do not
  add restrictive CORS rules that would block credentialed requests.

## Related constraints

- See `tauri/src-tauri/src/commands/file_download.rs` for the Rust
  side's SSRF guard (only loopback host, port 8000, and the two
  allowed path prefixes are accepted).
- Mirrors the pattern established by `fetchArtifactViaTauri` /
  `artifact_fetch.rs` for Rust-proxied HTTP.
