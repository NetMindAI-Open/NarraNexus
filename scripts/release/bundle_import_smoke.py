"""
@file_name: bundle_import_smoke.py
@author: NarraNexus
@date: 2026-09-10
@description: Import smoke test for a freshly populated interpreter — imports
              every sidecar entrypoint the desktop app launches and fails on
              ANY exception. Run by scripts/release/build-desktop.sh (step 3.1)
              with the BUNDLED interpreter, right after the dependencies land.

Assumes a POSIX bundle, because that is the only bundle we build (macOS dmg).
It matters for one name: `uvloop` is a conditional dependency in uv.lock
(`sys_platform != 'win32' and != 'cygwin'`, non-PyPy), so on a Windows bundle it
would be correctly absent and this script would fail on it. Whoever builds that
bundle should narrow the list by platform — NOT swallow the failure, which would
quietly drop httptools and websockets from the check as well.

Usage:
    <bundled python> scripts/release/bundle_import_smoke.py
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import sysconfig
import tempfile
import traceback
from pathlib import Path

# The four processes tauri/src-tauri/src/state.rs spawns in a packaged .app
# (`bundled_services()`), as importable module paths. `module_runner` is
# launched there by file path rather than `-m`; importing it as a module
# exercises the same import graph. Keep this list in lockstep with state.rs —
# a service added there and not here is a service whose dependency graph the
# build never checks (guarded by
# tests/release/test_desktop_build_uses_lock.py::test_smoke_entrypoints_match_state_rs).
ENTRYPOINTS = (
    "narranexus.platform.utils.db.sqlite_proxy_server",
    "narranexus.platform.module_system.module_runner",
    "narranexus.platform.module_system.run_worker_supervisor",
    # backend is launched as `-m uvicorn backend.main:app`, so BOTH halves have
    # to import: `backend/main.py` never imports uvicorn itself, which would
    # have left the server as the one piece of the bundle nothing checked.
    "uvicorn",
    "backend.main",
)

# `import uvicorn` does NOT reach these. uvicorn keeps its protocol/loop
# implementations as import-path STRINGS and resolves them through
# `import_from_string` inside `Config.load()`, so after importing uvicorn none
# of uvloop / httptools / websockets is in sys.modules (verified 2026-09-10).
# That matters most for `websockets`: it is a direct dependency of this project
# and NOTHING in src/ backend/ plugins/ packages/ imports it — uvicorn's
# websockets_impl is its only consumer. A broken wheel there does not raise; the
# server just logs "Unsupported upgrade request" per connection and the desktop
# app's chat WS is dead, with a green build behind it.
#
# Kept out of ENTRYPOINTS on purpose: that tuple is asserted equal to what
# state.rs launches (test_desktop_build_uses_lock.py), and these are libraries,
# not entrypoints.
LAZY_RUNTIME_IMPORTS = (
    "uvloop",
    "httptools",
    "websockets",
    # Plugin contributions name their implementation as an import string;
    # loading manifests alone never proves that BrowserModule can import.
    "narranexus_plugins.browser_module.browser_module",
    "narranexus.platform.browser._browser_impl.runtime_launch",
)


def _isolate_environment(scratch: str) -> None:
    """Point everything stateful at a scratch dir before the first import.

    `backend.main` builds its FastAPI app and loads the builtin plugins AT
    IMPORT TIME, so it reads a home directory, a plugin tree and a database
    URL. Left alone it would read the build machine's real ones — a green or
    red result that depends on who is building. Pinning them to a throwaway
    directory makes this gate deterministic, and is what lets it treat every
    exception as fatal instead of waving config errors through.
    """
    os.environ["HOME"] = scratch
    os.environ["NARRANEXUS_PLUGIN_HOME"] = os.path.join(scratch, "plugins")
    os.environ["NARRANEXUS_BROWSER_HOME"] = os.path.join(scratch, "browser")
    os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(scratch, 'smoke.db')}"
    # "desktop" is what process_manager.rs sets for these very processes;
    # importing under the surface the dmg actually runs is the point.
    os.environ["NARRA_SURFACE"] = "desktop"


def _non_relocatable_installs() -> list[str]:
    """Anything in this interpreter that only works on the machine that built it.

    The imports below CANNOT catch this, which is how v1.21.3 shipped broken:
    every workspace package was installed editable, i.e. as a `.pth` pointing at
    the build machine's source tree (/Users/runner/work/...). On the build
    machine that tree exists, so every import succeeded and the build went
    green; on a user's machine it does not, and the app died with
    `No module named 'narranexus.contracts'`. So check the install itself:
    no editable distributions, and no path hook leading out of this bundle.
    """
    # purelib AND platlib: identical on the macOS python-build-standalone we
    # bundle today, but an interpreter where they diverge must not let an
    # editable install hide in the one we did not look at. A set, so the common
    # case scans once.
    paths = sysconfig.get_paths()
    site_dirs = sorted({Path(paths["purelib"]), Path(paths["platlib"])})
    problems: list[str] = []
    for site_dir in site_dirs:
        for direct_url in sorted(site_dir.glob("*.dist-info/direct_url.json")):
            try:
                info = json.loads(direct_url.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if (info.get("dir_info") or {}).get("editable"):
                where = direct_url.parent.relative_to(site_dir)
                problems.append(f"{where}: editable install of {info.get('url')}")
        for pth in sorted(site_dir.glob("*.pth")):
            where = pth.relative_to(site_dir)
            if pth.name.startswith(("_editable_impl_", "__editable__")):
                problems.append(f"{where}: editable-install hook")
                continue
            try:
                content = pth.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                # Fail closed, but say which file and why rather than a traceback.
                problems.append(f"{where}: unreadable ({exc})")
                continue
            for raw in content.splitlines():
                # `site` executes lines starting with `import` + space OR tab;
                # those are code, not paths (setuptools ships one). Everything
                # else is a directory `site` appends to sys.path.
                if raw.startswith(("import ", "import\t")):
                    continue
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                # ANY absolute path is non-relocatable, including one that lies
                # inside this interpreter today: on the build machine
                # .../resources/python/... is "inside", and after the app moves
                # to /Applications that same path no longer exists. Legitimate
                # .pth entries are relative to site-packages.
                if Path(line).is_absolute():
                    problems.append(f"{where}: adds absolute path {line} to sys.path")
    return problems


def main() -> int:
    # Relocatability first: if the bundle depends on the build machine's
    # filesystem, the import results below are meaningless — they would pass
    # here and fail everywhere else.
    non_relocatable = _non_relocatable_installs()
    if non_relocatable:
        print("\nBundle is NOT relocatable — it depends on this machine's filesystem:",
              file=sys.stderr)
        for problem in non_relocatable:
            print(f"  ✗ {problem}", file=sys.stderr)
        print(
            "\nEvery package must be installed as real files in site-packages. For "
            "the workspace members that means `--no-editable` on `uv pip install`, "
            "not only on `uv export`.",
            file=sys.stderr,
        )
        return 1
    print("  ✓ bundle is relocatable (no editable installs, no external .pth paths)")

    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="nn-import-smoke-") as scratch:
        _isolate_environment(scratch)
        for module in ENTRYPOINTS + LAZY_RUNTIME_IMPORTS:
            try:
                importlib.import_module(module)
            except Exception as exc:  # noqa: BLE001 - see below
                # EVERY exception is fatal, not just ImportError. A dependency
                # that jumped a major usually does NOT announce itself as an
                # ImportError: starlette/fastapi surface as TypeError (changed
                # signature), a removed attribute as AttributeError, pydantic
                # as PydanticUserError, and a builtin plugin that fails to load
                # is re-raised by kernel.plugins.loader. Of the four packages
                # that actually drifted into the 2026-09-10 dmg, only mcp
                # happened to fail with an ImportError — an ImportError-only
                # gate would have caught one case in four.
                failures.append(f"{module}: {type(exc).__name__}: {exc}")
                traceback.print_exc()
            else:
                print(f"  ✓ {module}")

    if failures:
        print("\nBundle import smoke test FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  ✗ {failure}", file=sys.stderr)
        print(
            "\nThe interpreter in this bundle cannot import the app. Almost always a "
            "dependency-version mismatch: the install path must go through uv.lock "
            "(`uv export --locked` -> `uv pip install -r`), never the pyproject ranges.",
            file=sys.stderr,
        )
        return 1

    checked = len(ENTRYPOINTS) + len(LAZY_RUNTIME_IMPORTS)
    print(f"All {checked} bundle imports clean "
          f"({len(ENTRYPOINTS)} sidecar entrypoints + {len(LAZY_RUNTIME_IMPORTS)} lazily-resolved runtime deps).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
