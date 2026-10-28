---
code_file: src/narranexus/platform/browser/__main__.py
last_verified: 2026-09-23
stub: false
---

# Browser installer command entry

The real `python -m narranexus.platform.browser` entrypoint delegates to the
existing installer, so user recovery commands also work in the desktop's
relocated Python without uv, a checkout, Playwright, or a preinstalled browser.

Do not execute the private install module with `-m`: browser package imports
already load it, which would execute a second copy. This thin package entry
keeps install, status and cancel behavior in the owned installer and does not
start application services or alter browser sessions.
