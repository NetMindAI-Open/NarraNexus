"""
@file_name: locate.py
@author:
@date: 2026-09-22
@description: Find the installed browser runtime on disk, and prove it runs.

These are the two IO seams ``detect_runtime`` takes. Both are deliberately
strict, because the failure they guard against is the one users cannot get
themselves out of: a runtime reported as ``ready`` that cannot actually start
renders a blank panel with no explanation, whereas a runtime reported as
``absent`` shows the install card and a way forward.

So:

* the locator refuses a directory (half-extracted archive) and a file without
  the executable bit (interrupted download);
* the probe refuses a non-zero exit **even when the process printed
  something** — a browser that prints a banner and then dies on a missing
  library is not usable.

The install root lives under the user's data dir, never inside the app
bundle: the runtime has to survive app upgrades, and a notarised bundle is
read-only anyway.
"""
from __future__ import annotations

import os
import json
import platform
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional, Sequence, Tuple

#: Archive layouts seen across vendor builds, in preference order. First match
#: wins so that adding a layout for a new build never makes the answer
#: ambiguous.
#:
#: The Chrome for Testing entries are not guesses — they are what a real
#: archive unpacked to (verified 2026-09-22). The first version of this list
#: WAS a guess (``Chromium.app/.../Chromium``) and a 191 MB download landed
#: correctly and then reported "no executable found".
_CANDIDATE_RELPATHS: tuple[tuple[str, ...], ...] = (
    # Chrome for Testing — macOS
    ("chrome-mac-arm64", "Google Chrome for Testing.app", "Contents", "MacOS",
     "Google Chrome for Testing"),
    ("chrome-mac-x64", "Google Chrome for Testing.app", "Contents", "MacOS",
     "Google Chrome for Testing"),
    # Chrome for Testing — Linux / Windows
    ("chrome-linux64", "chrome"),
    ("chrome-win64", "chrome.exe"),
    # Plain Chromium snapshots (a mirror may serve these instead)
    ("chrome-mac", "Chromium.app", "Contents", "MacOS", "Chromium"),
    ("chrome-linux", "chrome"),
)

#: Escape hatch for tests and for users who keep large binaries elsewhere.
BROWSER_HOME_ENV = "NARRANEXUS_BROWSER_HOME"

#: ``(argv) -> (returncode, stdout, stderr)``
Runner = Callable[[Sequence[str]], Tuple[int, str, str]]

_PLATFORMS = {
    ("darwin", "arm64"): "mac-arm64",
    ("darwin", "aarch64"): "mac-arm64",
    ("darwin", "x86_64"): "mac-x64",
    ("darwin", "amd64"): "mac-x64",
    ("linux", "x86_64"): "linux64",
    ("linux", "amd64"): "linux64",
    ("windows", "amd64"): "win64",
    ("windows", "x86_64"): "win64",
    ("windows", "x86"): "win32",
    ("windows", "i386"): "win32",
    ("windows", "i686"): "win32",
}


def platform_key(*, system: Optional[str] = None, machine: Optional[str] = None) -> str:
    """Resolve the same vendor architecture for both install and discovery."""
    system = system or platform.system()
    machine = machine or platform.machine()
    key = _PLATFORMS.get((system.strip().lower(), machine.strip().lower()))
    if key is None:
        raise ValueError(f"no browser build published for {system}/{machine}")
    return key


def install_root() -> Path:
    """Where the runtime is installed.

    Under the user's data dir — NOT inside the app bundle. The runtime is a
    one-time ~150 MB download; putting it in the bundle would
    throw it away on every app upgrade, and a notarised bundle cannot be
    written to in the first place.
    """
    override = (os.environ.get(BROWSER_HOME_ENV) or "").strip()
    if override:
        root = Path(override).expanduser()
        return root if root.is_absolute() else Path.home() / root
    return Path.home() / ".narranexus" / "browser"


def locate_executable(*, root: Optional[Path] = None, plat: Optional[str] = None) -> Optional[Path]:
    """Return the browser executable under ``root``, or None.

    Args:
        root: Install root. Defaults to :func:`install_root`.

    Returns:
        The path, only if it is a regular file with the executable bit. A
        directory or a non-executable file means a broken install, which is
        reported as *absent* so the UI offers a re-install rather than a
        blank panel.
    """
    base = root if root is not None else install_root()
    if not base.is_dir():
        return None
    try:
        plat = plat or platform_key()
    except ValueError:
        return None
    # Only an atomically published generation is visible during a repair.
    receipt = base / f".runtime-{plat}.json"
    if receipt.exists():
        try:
            relative = Path(json.loads(receipt.read_text())["executable"])
            candidate = base / relative
            if relative.is_absolute() or not candidate.resolve().is_relative_to(base.resolve()):
                return None
            return candidate if _is_runnable(candidate) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None
    candidates = [parts for parts in _CANDIDATE_RELPATHS if parts[0] == f"chrome-{plat}"]
    if plat.startswith("mac-"):
        candidates.extend(parts for parts in _CANDIDATE_RELPATHS if parts[0] == "chrome-mac")
    elif plat == "linux64":
        candidates.extend(parts for parts in _CANDIDATE_RELPATHS if parts[0] == "chrome-linux")
    elif plat == "win32":
        candidates.append(("chrome-win32", "chrome.exe"))
    for parts in candidates:
        candidate = base.joinpath(*parts)
        if _is_runnable(candidate):
            return candidate
    return None


def _is_runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _default_runner(argv: Sequence[str]) -> Tuple[int, str, str]:
    if platform.system() == "Windows":
        # Windows Chrome does not print --version to stdout. Rendering its
        # internal version page proves the runtime starts without a user
        # profile, a GUI window, or an external website.
        with tempfile.TemporaryDirectory(prefix="narranexus-browser-probe-") as profile:
            proc = subprocess.run(
                [argv[0], "--headless=new", "--allow-chrome-scheme-url", "--disable-gpu", "--no-first-run",
                 "--no-default-browser-check", "--disable-background-networking",
                 f"--user-data-dir={profile}", "--dump-dom", "chrome://version"],
                capture_output=True, text=True, timeout=15,
            )
        version = re.search(r'id="version"[^>]*>\s*(\d+(?:\.\d+){3})', proc.stdout)
        return proc.returncode, f"Chromium {version[1]}" if version else "", proc.stderr
    proc = subprocess.run(  # noqa: S603 - fixed argv, never a shell string
        list(argv),
        capture_output=True,
        text=True,
        timeout=15,
    )
    return proc.returncode, proc.stdout, proc.stderr


def probe_version(executable: Path, *, runner: Runner = _default_runner) -> Optional[str]:
    """Run ``executable --version`` and return its output, or None on failure.

    Always an argv list, never a shell string: an install path containing
    spaces or shell metacharacters must not be interpreted.

    Returns None — not a raised exception — for every failure mode. The caller
    is a status query on an agent turn or a render request; it needs an
    answer, and ``classify_runtime`` turns None into ``absent``.
    """
    try:
        # The original ZIP extractor flattened macOS framework links into
        # text files. --version can still answer despite that damaged bundle,
        # so explicitly detect it and allow the installer to repair it.
        frameworks = executable.parent.parent / "Frameworks"
        for framework in frameworks.glob("*.framework"):
            versions = framework / "Versions"
            if versions.is_dir() and not (versions / "Current").is_dir():
                return None
        code, out, _err = runner([str(executable), "--version"])
    except Exception:
        return None
    if code != 0:
        return None
    text = (out or "").strip()
    return text if re.fullmatch(r"(?:Chromium|Google Chrome(?: for Testing)?|Chrome) \d+(?:\.\d+)*", text) else None
