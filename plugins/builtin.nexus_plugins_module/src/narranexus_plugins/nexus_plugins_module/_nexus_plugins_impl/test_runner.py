"""
@file_name: test_runner.py
@author: Bin Repo
@date: 2026-09-03
@description: ``plugin_test``: run the plugin's own pytest suite in a bounded subprocess and produce a signed report.

Bounds: wall-clock timeout, address-space and file-size rlimits on the
child, no network toggle beyond what the plugin already declares (the
executor sandbox owns network policy), and a fixed environment. The report
records the tree hash it was produced for, so ``plugin_register`` can refuse
a report that does not match the current files (spec §11.2).
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .validate import tree_hash

DEFAULT_TIMEOUT_S = 300.0
MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
FILE_SIZE_LIMIT_BYTES = 256 * 1024 * 1024
ROOT = Path(__file__).resolve().parents[6]  # the repo root (plugins/builtin.nexus_plugins_module/src/narranexus_plugins/nexus_plugins_module/_nexus_plugins_impl/ is six levels down); PYTHONPATH below needs ROOT/src AND ROOT


@dataclass
class TestReport:
    ok: bool
    tree_hash: str
    passed: int = 0
    failed: int = 0
    errors: int = 0
    duration_s: float = 0.0
    output_tail: str = ""
    report_hash: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _limits() -> None:  # pragma: no cover - runs in the child
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (MEMORY_LIMIT_BYTES, MEMORY_LIMIT_BYTES))
        resource.setrlimit(resource.RLIMIT_FSIZE, (FILE_SIZE_LIMIT_BYTES, FILE_SIZE_LIMIT_BYTES))
    except (ImportError, ValueError, OSError):
        pass


def _parse_counts(output: str) -> tuple[int, int, int]:
    import re

    passed = failed = errors = 0
    for m in re.finditer(r"(\d+) (passed|failed|error|errors)", output):
        n, kind = int(m.group(1)), m.group(2)
        if kind == "passed":
            passed = n
        elif kind == "failed":
            failed = n
        else:
            errors = n
    return passed, failed, errors


def sign(report: dict[str, Any]) -> str:
    body = {k: v for k, v in report.items() if k != "report_hash"}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def run_tests(plugin_dir: Path, *, timeout_s: float = DEFAULT_TIMEOUT_S, python: str | None = None, extra_env: dict[str, str] | None = None) -> TestReport:
    tests = plugin_dir / "tests"
    th = tree_hash(plugin_dir)
    if not tests.is_dir() or not any(tests.glob("test_*.py")):
        report = TestReport(ok=False, tree_hash=th, output_tail="no tests/test_*.py — a plugin without tests cannot be registered")
        report.report_hash = sign(report.as_dict())
        return report
    import tempfile

    test_home = Path(tempfile.mkdtemp(prefix="nx-plugin-test-home-"))
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", "/tmp"),
        "PYTHONPATH": os.pathsep.join([str(ROOT / "src"), str(ROOT)]),
        "NARRANEXUS_PLUGIN_HOME": str(test_home),
        "NARRANEXUS_DEPLOYMENT_MODE": "local",
        "PYTHONDONTWRITEBYTECODE": "1",
        # cwd is the plugin dir; without this `python -m pytest` puts it first on
        # sys.path and the plugin's own `backend/` shadows the platform package.
        "PYTHONSAFEPATH": "1",
        **(extra_env or {}),
    }
    cmd = [python or sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--maxfail=20", str(tests)]
    started = time.perf_counter()
    try:
        proc = subprocess.run(cmd, cwd=str(plugin_dir), env=env, capture_output=True, text=True, timeout=timeout_s, preexec_fn=_limits if os.name == "posix" else None, check=False)
        output = (proc.stdout or "") + (proc.stderr or "")
        passed, failed, errors = _parse_counts(output)
        ok = proc.returncode == 0 and failed == 0 and errors == 0 and passed > 0
    except subprocess.TimeoutExpired as exc:
        output = f"timeout after {timeout_s:.0f}s\n" + ((exc.stdout or b"").decode(errors="ignore") if isinstance(exc.stdout, bytes) else str(exc.stdout or ""))
        passed = failed = errors = 0
        ok = False
    import shutil

    shutil.rmtree(test_home, ignore_errors=True)
    report = TestReport(ok=ok, tree_hash=th, passed=passed, failed=failed, errors=errors, duration_s=time.perf_counter() - started, output_tail=output[-4000:])
    report.report_hash = sign(report.as_dict())
    return report


__all__ = ["DEFAULT_TIMEOUT_S", "TestReport", "run_tests", "sign"]
