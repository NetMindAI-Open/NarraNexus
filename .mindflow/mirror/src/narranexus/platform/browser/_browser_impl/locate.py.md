---
code_file: src/narranexus/platform/browser/_browser_impl/locate.py
last_verified: 2026-09-23
stub: false
---

# Runtime discovery and executable proof

Discovery and installation share one platform mapping. A stale ARM installation
must never win over the native x64 executable, and unsupported platforms must
not pick the first unrelated binary on disk. Known vendor browser paths are
selected explicitly; helper processes are never searched recursively.

A managed installation is visible through an atomically published per-platform
receipt. An invalid receipt or missing generation reports absent instead of
falling back to stale files. Existing unpacked vendor layouts can still be
detected directly when no managed receipt exists.

The default runtime directory is ~/.narranexus/browser, outside the read-only
app bundle, so upgrades preserve the optional runtime. NARRANEXUS_BROWSER_HOME
overrides this location; relative overrides are anchored to the user's home,
giving run.sh and Finder-launched desktop processes the same path despite
different working directories.

A runnable file alone is insufficient. POSIX uses an argv-based --version
probe, rejecting nonzero exits, blank output and non-browser output. Windows
Chrome does not print that flag to stdout, so it renders its internal version
page headlessly in a temporary profile. The probe never uses the user's profile
or a shell command. Errors return None for the runtime classifier.

The macOS framework's Versions/Current must resolve to a directory. The first
installer flattened that symlink into a text file, and a version banner alone
can still succeed on such a damaged bundle. Rejecting that state makes the
existing install button perform a repair instead of returning an idempotent
success forever. Live profile files and the old generation are left intact.

Native macOS execution was validated locally. Windows discovery and probe
arguments are covered by deterministic tests; native Windows execution still
requires a Windows host.
