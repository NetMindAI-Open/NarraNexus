---
code_file: src/narranexus/platform/browser/_browser_impl/downloader.py
last_verified: 2026-09-23
stub: false
---

# Optional browser installation

Chrome for Testing supplies a published platform/version manifest, avoiding
guessed browser revision URLs and any dependency on the Playwright package.
Chromium is downloaded only after an explicit install action and never enters
the application wheel or desktop resources.

NARRANEXUS_BROWSER_DOWNLOAD_HOST replaces the archive host while retaining
the vendor path. NARRANEXUS_BROWSER_MANIFEST_URL also allows the index itself
to be mirrored; replacing only the binary host would not help users unable to
reach the default index. Both sources require valid HTTPS URLs.
The manual installer can also pass an explicit manifest URL to a downloader
instance; this takes precedence without mutating process-wide environment.
The existing environment contract remains the default for UI/MCP installers.

Partials are keyed by platform, version and complete URL, preventing a changed
build or mirror from reusing unrelated bytes. Transport failures retain the
partial; corrupt archives, unsafe entries and invalid executables are discarded.
HTTP ranges, response lengths and archive CRCs are checked. The publisher
does not provide a separate signed checksum through this index: HTTPS and ZIP
integrity checks must not be described as publisher-signature verification.

Extraction uses a fresh generation under the install root. Regular files are
written before symlinks, executable permissions are restored, and macOS
framework symlinks are preserved. Traversal, special files, duplicate paths,
escaping links and archive writes through links are refused. Flattening these
links was observed in the original local install and breaks the framework.

Only a successful browser probe with the manifest's exact version publishes an
atomic per-platform receipt. Discovery cannot see an unfinished generation or
mistake an older broken executable for the new install. Failed/cancelled stages
are removed. Prior published generations remain intact for existing sessions.
The extraction worker finishes before cancellation releases its lock or removes
its staging directory, because cancelling an asyncio waiter cannot stop a thread.

The OS lock and atomic progress/outcome snapshots come from install.py.
Independent backend/MCP processes share progress, cancellation and a single
outcome. Runtime discovery and installation use the same platform mapping.

Validation included a real 191,015,853-byte macOS archive: Chrome 153.0.8010.52
ran successfully after extraction, with all five framework symlinks preserved.
Deterministic tests additionally exercise resume, corrupt archives, stalled
transport cancellation, process crashes and cross-process state.
