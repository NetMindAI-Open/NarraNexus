"""
@file_name: lifecycle.py
@author: Bin Liang
@date: 2026-09-03
@description: ``registry.json`` — the only writer of user-plugin state, with LKG snapshots, a state machine and the safe-mode marker.

Why a file and not the database: rollback must work when nothing else does
(no LLM, no UI, no DB), so the truth about which plugins exist and whether
they are enabled is a JSON file the kernel can rewrite with plain file
operations. Every write goes through ``RegistryStore.update``: take the file
lock, write a temp file, replace; ``snapshot_lkg()`` copies the file to ``registry.lkg.json`` when a boot reached health, and
``os.replace`` it — a crash mid-write leaves either the old or the new file,
never a torn one, and the LKG copy is always the last state that booted HEALTHILY.

States (spec §9.1): ``registered → validated → enabled → active`` on the happy
path; ``incompatible / blocked / missing / deps_missing / crashed / disabled /
slow`` are the exceptional ones. ``record_crash`` disables a plugin on its
second crash. ``BootMarker`` implements the startup-crash counter: a marker
that is still there when the next boot starts means the previous boot never
reached health, and two of those in a row flip ``safe_mode``.
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from narranexus.contracts import PluginError
from narranexus.kernel.plugins.paths import boot_marker_path, lkg_path, registry_path

State = Literal[
    "registered", "validated", "enabled", "active",
    "incompatible", "blocked", "missing", "deps_missing", "crashed", "disabled", "slow",
]
Scope = str  # "global" | "agent:<id>"

# Allowed transitions. Exceptional states may be entered from anywhere; the
# happy path is strictly ordered; leaving an exceptional state goes back to
# ``registered`` (re-validate) or ``disabled``.
_HAPPY: tuple[State, ...] = ("registered", "validated", "enabled", "active")
_EXCEPTIONAL: frozenset[State] = frozenset({"incompatible", "blocked", "missing", "deps_missing", "crashed", "disabled", "slow"})
CRASH_DISABLE_THRESHOLD = 2
SAFE_MODE_THRESHOLD = 2


class RegistryError(PluginError):
    """Invalid transition or corrupt registry file."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Source(_Strict):
    type: Literal["github", "github_repo", "local"] = "local"
    repo: str = ""
    tag: str = ""
    ref: str = ""
    commit: str = ""
    assets_sha256: dict[str, str] = Field(default_factory=dict)


class PluginRecord(_Strict):
    path: str
    mode: Literal["copy", "link"] = "copy"
    source: Source = Field(default_factory=Source)
    enabled: bool = True
    installed_version: str = ""
    scope: Scope = "global"
    state: State = "registered"
    last_error: str | None = None
    crash_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    permissions_acknowledged: bool = False
    # the permission tokens (network:*, filesystem:<path>, subprocess, env:<VAR>) the
    # acknowledgement covered — an upgrade that declares more must be acknowledged again
    acknowledged_permissions: list[str] = Field(default_factory=list)
    installed_by: str = "user"
    installed_at: str = ""
    # per-role: when the plugin was last activated / the last error, for the factory page
    activated_at: str = ""


class BisectState(_Strict):
    """Halving search over the enabled user plugins (spec §9.5)."""

    candidates: list[str] = Field(default_factory=list)  # still suspected
    trial: list[str] = Field(default_factory=list)  # currently enabled half
    cleared: list[str] = Field(default_factory=list)


class RegistryFile(_Strict):
    schema_version: int = Field(default=1, alias="schema")
    plugins: dict[str, PluginRecord] = Field(default_factory=dict)
    builtin_overrides: dict[str, dict[str, Any]] = Field(default_factory=dict)
    last_known_good: str = "registry.lkg.json"
    safe_mode: bool = False
    safe_mode_reason: str = ""
    bisect: BisectState | None = None

    def enabled_ids(self) -> list[str]:
        return sorted(pid for pid, rec in self.plugins.items() if rec.enabled and rec.state not in _EXCEPTIONAL)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@contextlib.contextmanager
def _locked(path: Path) -> Iterator[None]:
    """Advisory file lock next to the registry (fcntl where available)."""
    lock = path.with_suffix(path.suffix + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - non-POSIX
            pass
        yield
    finally:
        os.close(fd)


def _atomic_write_json(path: Path, data: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


@dataclass
class RegistryStore:
    """Read/modify/write access to ``registry.json`` with LKG snapshots."""

    path: Path = field(default_factory=registry_path)
    lkg: Path = field(default_factory=lkg_path)

    # ------------------------------------------------------------- reading

    def read(self) -> RegistryFile:
        if not self.path.exists():
            return RegistryFile()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return RegistryFile.model_validate(raw)
        except (OSError, ValueError, ValidationError) as exc:
            raise RegistryError(f"{self.path}: corrupt registry file: {exc}") from exc

    def read_lkg(self) -> RegistryFile | None:
        if not self.lkg.exists():
            return None
        return RegistryFile.model_validate(json.loads(self.lkg.read_text(encoding="utf-8")))

    # ------------------------------------------------------------- writing

    def update(self, mutate: Callable[[RegistryFile], None]) -> RegistryFile:
        """Locked read → mutate → atomic replace. Returns the new state.

        Does NOT touch the LKG copy: a boot itself writes (rejections, crash
        counts, validated/enabled transitions), so "snapshot before every
        write" made the LKG the state that included the plugin that just
        broke the boot. The snapshot is taken by ``snapshot_lkg()`` when the
        host reports health (BootReport.mark_healthy) — the only moment a
        state is proven good.
        """
        with _locked(self.path):
            current = self.read()
            mutate(current)
            _atomic_write_json(self.path, current.model_dump(by_alias=True))
            return current

    def snapshot_lkg(self) -> bool:
        """Copy the current registry to ``registry.lkg.json``; False when there is no registry yet."""
        with _locked(self.path):
            if not self.path.exists():
                return False
            shutil.copyfile(self.path, self.lkg)
            return True

    def rollback_to_lkg(self) -> RegistryFile:
        """Restore the last-known-good file (a pure file operation; never needs the app)."""
        with _locked(self.path):
            if not self.lkg.exists():
                raise RegistryError(f"{self.lkg}: no last-known-good snapshot to roll back to")
            data = json.loads(self.lkg.read_text(encoding="utf-8"))
            state = RegistryFile.model_validate(data)
            _atomic_write_json(self.path, state.model_dump(by_alias=True))
            return state

    # ------------------------------------------------------- state machine

    def transition(self, plugin_id: str, state: State, *, error: str | None = None) -> PluginRecord:
        def _mutate(reg: RegistryFile) -> None:
            rec = reg.plugins.get(plugin_id)
            if rec is None:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            _check_transition(rec.state, state)
            rec.state = state
            if state in _EXCEPTIONAL:
                rec.last_error = error
            else:
                rec.last_error = None
            if state == "active":
                rec.activated_at = _now()

        return self.update(_mutate).plugins[plugin_id]

    def record_crash(self, plugin_id: str, error: str) -> PluginRecord:
        """Count a crash; the second one disables the plugin (spec §9.5)."""

        def _mutate(reg: RegistryFile) -> None:
            rec = reg.plugins.get(plugin_id)
            if rec is None:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            rec.crash_count += 1
            rec.last_error = error
            if rec.crash_count >= CRASH_DISABLE_THRESHOLD:
                rec.enabled = False
                rec.state = "disabled"
                rec.warnings.append(f"auto-disabled after {rec.crash_count} crashes: {error}")
            else:
                rec.state = "crashed"

        return self.update(_mutate).plugins[plugin_id]

    def register(self, plugin_id: str, record: PluginRecord) -> PluginRecord:
        def _mutate(reg: RegistryFile) -> None:
            if plugin_id in reg.plugins:
                raise RegistryError(f"plugin {plugin_id!r} is already registered")
            rec = record.model_copy(update={"state": "registered", "installed_at": record.installed_at or _now()})
            reg.plugins[plugin_id] = rec

        return self.update(_mutate).plugins[plugin_id]

    def unregister(self, plugin_id: str) -> None:
        def _mutate(reg: RegistryFile) -> None:
            if plugin_id not in reg.plugins:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            del reg.plugins[plugin_id]

        self.update(_mutate)

    def clear_crashes(self, plugin_id: str) -> PluginRecord:
        """A clean boot after a crash: the crash budget (spec §9.5 "the second one
        disables") counts consecutive crashes, so a recovered plugin starts again at
        zero and the auto-disable warning goes with it."""
        def _mutate(reg: RegistryFile) -> None:
            rec = reg.plugins.get(plugin_id)
            if rec is None:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            rec.crash_count = 0
            rec.last_error = None
            rec.warnings = [w for w in rec.warnings if not w.startswith("auto-disabled after ")]
        return self.update(_mutate).plugins[plugin_id]

    def set_enabled(self, plugin_id: str, enabled: bool) -> PluginRecord:
        def _mutate(reg: RegistryFile) -> None:
            rec = reg.plugins.get(plugin_id)
            if rec is None:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            rec.enabled = enabled
            rec.state = "registered" if enabled else "disabled"
            if enabled:
                rec.crash_count = 0
                rec.last_error = None

        return self.update(_mutate).plugins[plugin_id]

    def set_safe_mode(self, on: bool, *, reason: str = "") -> RegistryFile:
        def _mutate(reg: RegistryFile) -> None:
            reg.safe_mode = on
            reg.safe_mode_reason = reason if on else ""

        return self.update(_mutate)


def _check_transition(current: State, new: State) -> None:
    if new in _EXCEPTIONAL:
        return
    if current in _EXCEPTIONAL:
        if new == "registered":
            return
        raise RegistryError(f"cannot go from {current!r} to {new!r}; re-validate first (→ registered)")
    if _HAPPY.index(new) not in (_HAPPY.index(current), _HAPPY.index(current) + 1):
        raise RegistryError(f"cannot go from {current!r} to {new!r}")


@dataclass
class BootMarker:
    """Startup-crash counter for one host role.

    ``enter`` writes the marker with the count of consecutive failed boots
    seen so far (a leftover marker at ``enter`` time means the previous boot
    never called ``exit``); ``exit`` removes it once the process is healthy.
    ``safe_mode_due`` is true when two consecutive boots failed.
    """

    role: str
    path: Path | None = None

    def __post_init__(self) -> None:
        if self.path is None:
            self.path = boot_marker_path(self.role)

    def enter(self) -> int:
        assert self.path is not None
        failures = 0
        if self.path.exists():
            try:
                failures = int(self.path.read_text().strip() or "0") + 1
            except ValueError:
                failures = 1
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(str(failures))
        return failures

    def exit(self) -> None:
        assert self.path is not None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def consecutive_failures(self) -> int:
        assert self.path is not None
        if not self.path.exists():
            return 0
        try:
            return int(self.path.read_text().strip() or "0")
        except ValueError:
            return 1

    @property
    def safe_mode_due(self) -> bool:
        return self.consecutive_failures() >= SAFE_MODE_THRESHOLD


__all__ = [
    "CRASH_DISABLE_THRESHOLD",
    "SAFE_MODE_THRESHOLD",
    "BisectState",
    "BootMarker",
    "PluginRecord",
    "RegistryError",
    "RegistryFile",
    "RegistryStore",
    "Source",
    "State",
]
