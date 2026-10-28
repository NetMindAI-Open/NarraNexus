---
code_file: src/narranexus/kernel/plugins/install/sources.py
last_verified: 2026-09-09
stub: false
---

## 2026-09-03（批 2c）— 三种来源，一种 `FetchResult`

GitHub Release（固定资产集 manifest/backend.zip/plugin.js/styles.css/versions.json；tag 为准，manifest 版本不一致
只记 warning）、GitHub repo ref（tarball + 记 commit sha）、本地目录（link 原地 / copy 复制且忽略 pyenv 等）。
压缩包解压带路径穿越守卫（越界即中止；tar 里的链接拒绝）；网络走可注入的 `httpx.Client`（测试用
MockTransport）；资产上限 200MB。`parse_source_spec` 解析 `owner/repo[@tag]`、`owner/repo#ref`、GitHub URL、本地路径。

## 2026-09-07 — streamed downloads; bounded extraction

_get streams and aborts the moment the body passes MAX_ASSET_BYTES (the old code read the whole body first, so the limit protected nothing); JSON responses go through _get_json. extract_zip/extract_tarball enforce MAX_EXTRACT_BYTES / MAX_ARCHIVE_MEMBERS on declared sizes AND bytes actually written (a header can lie) — a 200 MB zip bomb can no longer fill the disk.

## 2026-09-07 — _http(): self-created clients are closed; 'owner/..' refused

fetch() wraps _fetch() in _http(client): a caller's client is used as-is, a client of our own is closed on exit (they leaked before). _validate_repo refuses '.'/'..'/dot-leading segments (owner/.. rewrote the API path).

## 2026-09-09 — extract_zip: corrupt archive → SourceError

`zipfile.ZipFile(io.BytesIO(data))` on a truncated/corrupt release asset raised a bare BadZipFile — the only refusal in this module that was not a SourceError, so the install pipeline's classification and wording did not apply and the user saw a raw exception unrelated to the plugin source. Converted on the open (the extraction loop is unchanged); pinned by test_sources.py::test_a_corrupt_zip_is_a_source_error. Found by the B-30 `zipfile.ZipFile(` sweep (table in the skill_module mirror).
