"""
@file_name: test_runtime.py
@author:
@date: 2026-09-22
@description: Tests for the in-app browser runtime detector.

The Chromium runtime is NOT shipped in the dmg (Owner decision 2026-09-22) —
the user installs it once from the UI. Everything downstream (the agent tool
gate, the stream renderer, the ContextData hook) branches on the status this
module reports, so the classifier is tested exhaustively and with no IO.

`detect_runtime` takes its two IO seams as arguments so the decision table
above it stays pure and the probe can be driven deterministically.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.platform.browser._browser_impl.runtime import (
    BrowserRuntimeStatus,
    classify_runtime,
    detect_runtime,
)


EXE = Path("/tmp/does-not-need-to-exist/chrome")


# ── pure classifier ──────────────────────────────────────────────────────────


def test_no_executable_is_absent():
    s = classify_runtime(executable=None, version=None, installing=False)
    assert s.state == "absent"
    assert s.reason == "no-executable"
    assert s.executable is None
    assert s.version is None


def test_executable_present_but_probe_failed_is_absent_not_ready():
    """A file on disk is not proof of a working browser.

    Half-extracted downloads and quarantined binaries both leave an
    executable that cannot actually start; reporting `ready` for those
    sends the user to a blank panel with no way to self-diagnose.
    """
    s = classify_runtime(executable=EXE, version=None, installing=False)
    assert s.state == "absent"
    assert s.reason == "probe-failed"
    assert s.executable == EXE


def test_executable_plus_version_is_ready():
    s = classify_runtime(executable=EXE, version="Chromium 152.0.7977.54", installing=False)
    assert s.state == "ready"
    assert s.reason == "ready"
    assert s.version == "Chromium 152.0.7977.54"


def test_installing_reported_while_not_yet_usable():
    s = classify_runtime(executable=None, version=None, installing=True)
    assert s.state == "installing"
    assert s.reason == "installing"


def test_ready_wins_over_installing():
    """Install is idempotent: a usable runtime stays usable.

    If a re-install is somehow in flight over an already-working runtime,
    the user must not be blocked behind a progress bar.
    """
    s = classify_runtime(executable=EXE, version="Chromium 152", installing=True)
    assert s.state == "ready"


@pytest.mark.parametrize("blank", ["", "   ", "\n"])
def test_blank_version_string_is_not_a_successful_probe(blank):
    s = classify_runtime(executable=EXE, version=blank, installing=False)
    assert s.state == "absent"
    assert s.reason == "probe-failed"


def test_status_is_serialisable_for_the_api():
    s = classify_runtime(executable=EXE, version="Chromium 152", installing=False)
    d = s.to_dict()
    assert d["state"] == "ready"
    assert d["version"] == "Chromium 152"
    assert d["executable"] == str(EXE)
    assert set(d) == {"state", "reason", "version", "executable"}


def test_absent_status_serialises_executable_as_none():
    d = classify_runtime(executable=None, version=None, installing=False).to_dict()
    assert d["executable"] is None


# ── detect_runtime: the two IO seams ─────────────────────────────────────────


def test_detect_runs_probe_only_when_a_locator_found_something():
    """Probing costs a process spawn; skip it when there is nothing to probe."""
    calls: list[Path] = []

    def probe(p: Path) -> str | None:
        calls.append(p)
        return "Chromium 152"

    s = detect_runtime(locate=lambda: None, probe=probe, installing=False)
    assert s.state == "absent"
    assert calls == []


def test_detect_probes_the_located_executable():
    s = detect_runtime(locate=lambda: EXE, probe=lambda p: f"Chromium from {p.name}", installing=False)
    assert s.state == "ready"
    assert s.version == "Chromium from chrome"


def test_detect_treats_a_raising_probe_as_absent():
    """A probe that throws (permission denied, Gatekeeper kill) is a failure,
    not a crash of whatever asked for the status."""

    def probe(_p: Path) -> str | None:
        raise OSError("Gatekeeper killed it")

    s = detect_runtime(locate=lambda: EXE, probe=probe, installing=False)
    assert s.state == "absent"
    assert s.reason == "probe-failed"


def test_detect_treats_a_raising_locator_as_absent():
    def locate() -> Path | None:
        raise OSError("registry unreadable")

    s = detect_runtime(locate=locate, probe=lambda _p: "x", installing=False)
    assert s.state == "absent"
    assert s.reason == "no-executable"


def test_detect_never_caches():
    """Status must be recomputed per call — the user can delete the runtime
    between two uses."""
    seq = iter([EXE, None])
    probe_result = iter(["Chromium 152", None])

    first = detect_runtime(
        locate=lambda: next(seq), probe=lambda _p: next(probe_result), installing=False
    )
    second = detect_runtime(
        locate=lambda: next(seq), probe=lambda _p: next(probe_result), installing=False
    )
    assert first.state == "ready"
    assert second.state == "absent"


def test_status_equality_is_by_value():
    a = classify_runtime(executable=EXE, version="v", installing=False)
    b = classify_runtime(executable=EXE, version="v", installing=False)
    assert a == b
    assert isinstance(a, BrowserRuntimeStatus)
