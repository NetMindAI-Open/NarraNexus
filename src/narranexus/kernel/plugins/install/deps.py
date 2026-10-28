"""
@file_name: deps.py
@author: Bin Liang
@date: 2026-09-03
@description: Install a plugin's pip dependencies into its private directory — wheels only, no build scripts, bounded time, allow-listed indexes.

``uv pip install --target`` when ``uv`` is on the PATH, else ``python -m
pip install --target``; both with ``--only-binary=:all:`` (no sdist builds,
so no ``setup.py`` runs), ``--no-deps`` NOT set (dependencies resolve), a
120 s deadline, and the index restricted to PyPI plus what the manifest
declared. The subprocess runner is injectable so tests never hit the
network or a package manager.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from narranexus.contracts import PluginError

DEFAULT_TIMEOUT_S = 120.0
PYPI = "https://pypi.org/simple"


class DepsError(PluginError):
    pass


@dataclass(frozen=True)
class DepsResult:
    target: Path
    requirements: tuple[str, ...]
    command: tuple[str, ...]
    output_tail: str


Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


# What the installer subprocess inherits. An allow-list, not the whole
# environment: PYTHONPATH / VIRTUAL_ENV / PIP_TARGET would make pip install
# into the HOST environment (the reason the env was ever cleared) — but a
# fully empty env dropped the proxy, the corporate CA bundle, HOME (cache
# location) and SYSTEMROOT (Windows cannot start a process without it), so
# every dependency install failed behind a proxy with a bare network
# traceback that pointed nowhere.
_PASSTHROUGH = frozenset({
    "PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "no_proxy", "all_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE",
})
_PASSTHROUGH_PREFIXES = ("UV_", "PIP_")
_NEVER = frozenset({"PYTHONPATH", "VIRTUAL_ENV", "PIP_TARGET", "PIP_PREFIX", "PIP_USER", "UV_PROJECT_ENVIRONMENT"})


def subprocess_env() -> dict[str, str]:
    """The allow-listed environment for the dependency installer."""
    import os

    env = {
        k: v
        for k, v in os.environ.items()
        if (k in _PASSTHROUGH or k.startswith(_PASSTHROUGH_PREFIXES)) and k not in _NEVER
    }
    env["PIP_NO_INPUT"] = "1"
    return env


def _default_runner(cmd: Sequence[str], timeout: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout, env=subprocess_env(), check=False)


def build_command(requirements: Sequence[str], target: Path, *, indexes: Sequence[str] = ()) -> tuple[str, ...]:
    uv = shutil.which("uv")
    base: list[str] = [uv, "pip", "install", "--python", sys.executable] if uv else [sys.executable, "-m", "pip", "install"]
    cmd = base + ["--target", str(target), "--only-binary=:all:", "--index-url", PYPI]
    for extra in indexes:
        if extra != PYPI:
            cmd += ["--extra-index-url", extra]
    cmd += list(requirements)
    return tuple(cmd)


def install_deps(
    requirements: Sequence[str],
    target: Path,
    *,
    indexes: Sequence[str] = (),
    timeout_s: float = DEFAULT_TIMEOUT_S,
    runner: Runner | None = None,
) -> DepsResult | None:
    """Install ``requirements`` into ``target``. Returns ``None`` when there is nothing to install."""
    reqs = tuple(r.strip() for r in requirements if r.strip())
    if not reqs:
        return None
    for r in reqs:
        if r.startswith("-") or any(ch in r for ch in " ;"):
            raise DepsError(f"refusing requirement {r!r}: options and spaces are not allowed")
    for idx in indexes:
        if not idx.startswith("https://"):
            raise DepsError(f"index {idx!r} must be https")
    target.mkdir(parents=True, exist_ok=True)
    cmd = build_command(reqs, target, indexes=indexes)
    run = runner or _default_runner
    try:
        proc = run(cmd, timeout_s)
    except subprocess.TimeoutExpired:
        raise DepsError(f"dependency install exceeded {timeout_s:.0f}s") from None
    except OSError as exc:
        raise DepsError(f"cannot run the package manager: {exc}") from exc
    tail = (proc.stdout or "")[-2000:] + (proc.stderr or "")[-2000:]
    if proc.returncode != 0:
        raise DepsError(f"dependency install failed (rc={proc.returncode}): {tail.strip()[-800:]}")
    return DepsResult(target=target, requirements=reqs, command=cmd, output_tail=tail)


__all__ = ["DEFAULT_TIMEOUT_S", "DepsError", "DepsResult", "PYPI", "Runner", "build_command", "install_deps"]
