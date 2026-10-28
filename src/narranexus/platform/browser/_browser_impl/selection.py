"""
@file_name: selection.py
@date: 2026-09-23
@description: Explicit, shared selection of managed Chromium or installed stable Chrome.
"""
from __future__ import annotations

import json
import os
import platform
import tempfile
from pathlib import Path
from typing import Literal

from narranexus.kernel.deployment import is_cloud_mode
from narranexus.platform.browser._browser_impl.locate import (
    install_root,
    locate_executable,
    probe_version,
)

BrowserSource = Literal["managed", "system"]
BrowserMode = Literal["headless", "headed"]


def system_candidates() -> list[Path]:
    """Known stable Chrome executables, never a personal profile or a PATH search."""
    system = platform.system()
    if system == "Darwin":
        suffix = Path("Google Chrome.app/Contents/MacOS/Google Chrome")
        return [Path("/Applications") / suffix, Path.home() / "Applications" / suffix]
    if system == "Linux":
        return [Path("/opt/google/chrome/chrome"), Path("/usr/bin/google-chrome-stable"),
                Path("/usr/bin/google-chrome")]
    if system == "Windows":
        return [Path(base) / "Google/Chrome/Application/chrome.exe"
                for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
                if (base := os.environ.get(key)) and Path(base).is_absolute()]
    return []


def locate_system_executable() -> Path | None:
    if is_cloud_mode():
        return None
    return next((path for path in system_candidates()
                 if path.is_file() and os.access(path, os.X_OK)), None)


def read_source() -> BrowserSource:
    """Read on every new launch so API and MCP processes share the same choice."""
    if is_cloud_mode():
        return "managed"
    try:
        value = json.loads((install_root() / "runtime-source.json").read_text())
    except FileNotFoundError:
        return "managed"
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid browser source preference") from exc
    if not isinstance(value, dict) or set(value) != {"source"} or value["source"] not in ("managed", "system"):
        raise ValueError("Invalid browser source preference")
    return value["source"]


def locate_selected() -> Path | None:
    return locate_system_executable() if read_source() == "system" else locate_executable()


def read_mode() -> BrowserMode:
    """Read the local launch preference; cloud never opens native windows."""
    if is_cloud_mode():
        return "headless"
    try:
        value = json.loads((install_root() / "runtime-mode.json").read_text())
    except FileNotFoundError:
        return "headless"
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid browser mode preference") from exc
    if not isinstance(value, dict) or set(value) != {"mode"} or value["mode"] not in ("headless", "headed"):
        raise ValueError("Invalid browser mode preference")
    return value["mode"]


def source_view() -> dict:
    executable = locate_system_executable()
    return {"source": read_source(), "mode": read_mode(), "editable": not is_cloud_mode(),
            "system_executable": str(executable) if executable else None}


def save_source(source: BrowserSource) -> None:
    """Publish one complete choice atomically; never close an existing session."""
    if is_cloud_mode():
        raise PermissionError("Browser source selection is available only in local mode")
    if source not in ("managed", "system"):
        raise ValueError("Invalid browser source")
    if source == "system":
        executable = locate_system_executable()
        if executable is None:
            raise ValueError("Installed Google Chrome was not found")
        if probe_version(executable) is None:
            raise ValueError("Installed Google Chrome could not start")
    _save_preference("source", source)


def save_mode(mode: BrowserMode) -> None:
    """Save only the next launch mode, keeping the source and profile intact."""
    if is_cloud_mode():
        raise PermissionError("Browser mode selection is available only in local mode")
    if mode not in ("headless", "headed"):
        raise ValueError("Invalid browser mode")
    _save_preference("mode", mode)


def _save_preference(key: Literal["source", "mode"], value: str) -> None:
    # Separate documents avoid lost updates between independent preferences.
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".runtime-{key}-", suffix=".json", dir=root)
    path = Path(temporary)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump({key: value}, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(path, root / f"runtime-{key}.json")
    finally:
        path.unlink(missing_ok=True)
