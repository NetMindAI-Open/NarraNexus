---
code_file: src/narranexus/platform/browser/_browser_impl/runtime.py
last_verified: 2026-09-23
stub: false
---

# One runtime readiness decision

Chromium is an optional user installation, not a desktop bundle resource.
Agent tools, the browser panel and context hooks must agree whether to offer
installation or proceed. BrowserService supplies the locator, executable probe
and coordinator state to this single classifier.

Presence on disk is not readiness. A successful browser probe is required;
missing executables and failed probes retain distinct reasons so the user can
act on them. Blank output is never a successful probe. Locator/probe exceptions
become an absent status rather than breaking a render request or agent turn.

Readiness is recomputed on every call, since the user can remove the runtime
between uses. A working browser outranks an install in progress: repair activity
must not hide an already usable runtime. Machine-wide installing state comes
from the real downloader's OS lock, through InstallCoordinator; stale progress
files do not count as an active installation.

The classifier's injected I/O keeps these decisions independent of archive
layout, transport, platform-specific probing and eventual remote runtimes.
