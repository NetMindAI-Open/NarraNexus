---
code_file: src/narranexus/platform/browser/_browser_impl/download.py
last_verified: 2026-09-23
stub: false
---

# Resumable browser transfers

A failed optional browser installation must preserve useful partial bytes.
The transfer loop requests the existing file's offset and reports progress
against the complete archive size, including bytes from an earlier attempt.
A declared short response is an error, not a successful download.

The concrete HTTP adapter in downloader.py validates Content-Range and rejects
ignored ranges before any response bytes can be appended. RestartDownload is
an internal signal to discard an incompatible partial and request byte zero.
A completed partial can be reused when the server answers 416 with its exact
size. Unknown lengths remain indeterminate.

Cancellation is checked before opening transport and while waiting for each
chunk. Outstanding requests are cancelled and closed promptly even when the
server stalls. Partial bytes remain available for a later attempt; cancellation
does not become an arbitrary timeout on the agent's work.

HTTPS URL validation is shared by the vendor manifest, archive URLs and mirror
configuration. The older revision URL helper has no Playwright Python
dependency; the production installer resolves Chrome for Testing builds.
