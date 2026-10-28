"""
@file_name: distribution.py
@author: Bin Liang
@date: 2026-09-04
@description: Resolve a ``narranexus-dist.json`` against this host (spec section 19): which plugins make up the distribution (builtins by id and version range, bundled plugins by path), what it excludes, the engine range, the auth choice, the distribution-layer slot bindings — and the doctor / lock views of that resolution. Pure computation over manifests; no plugin code is imported here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from narranexus.contracts import ManifestError
from narranexus.contracts.distribution import (
    DISTRIBUTION_FILENAME,
    DistributionError,
    DistributionSpec,
    parse_distribution,
)
from narranexus.kernel.plugins.bindings import referenced_plugins, BindingSource, Layer
from narranexus.kernel.plugins.compat import Range
from narranexus.kernel.plugins.manifest import Manifest

ENV_DISTRIBUTION = "NARRANEXUS_DIST"
LOCK_FILENAME = "narranexus-dist.lock.json"
AUTH_SLOT = "kernel.auth"


@dataclass(frozen=True)
class PluginPick:
    """One plugin the distribution selected."""

    id: str
    version: str
    source: str  # "builtin" | "path"
    manifest: Manifest
    path: Path | None = None  # bundled plugins only


@dataclass
class DistributionResolution:
    spec: DistributionSpec
    base_dir: Path
    picks: list[PluginPick] = field(default_factory=list)
    excluded: tuple[str, ...] = ()
    problems: list[str] = field(default_factory=list)
    bindings: BindingSource = field(default_factory=lambda: BindingSource(Layer.DISTRIBUTION, {}))

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def manifests(self) -> list[Manifest]:
        return [p.manifest for p in self.picks]

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        return tuple(p.id for p in self.picks)

    def pick(self, plugin_id: str) -> PluginPick | None:
        return next((p for p in self.picks if p.id == plugin_id), None)

    def raise_for_problems(self) -> None:
        if self.problems:
            raise DistributionError(f"{self.spec.id}: " + "; ".join(self.problems))


def find_distribution(environ: Mapping[str, str] | None = None) -> Path | None:
    """The distribution this process runs, from ``NARRANEXUS_DIST`` (a file or its directory); ``None`` = all builtins."""
    raw = (environ if environ is not None else os.environ).get(ENV_DISTRIBUTION, "").strip()
    if not raw:
        return None
    return distribution_file(Path(raw))


def distribution_file(path: Path) -> Path:
    return path / DISTRIBUTION_FILENAME if path.is_dir() else path


def load_distribution(path: Path) -> tuple[DistributionSpec, Path]:
    """Parse the file (or ``<dir>/narranexus-dist.json``); returns the spec and the directory bundled paths are relative to."""
    file = distribution_file(path)
    if not file.is_file():
        raise DistributionError(f"{file}: not found")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DistributionError(f"{file}: {exc}") from None
    if not isinstance(data, dict):
        raise DistributionError(f"{file}: top level must be an object")
    return parse_distribution(data, origin=str(file)), file.parent


def _contribution_names(manifest: Any) -> set[str]:
    """Contribution names a manifest could register (best effort: the symbol's last path segment), for validating name-form bindings without importing the plugin."""
    names: set[str] = set()
    for value in manifest.provides.values():
        for spec in ([value] if isinstance(value, str) else value):
            names.add(str(spec).rsplit(":", 1)[-1])
    return names


def resolve_distribution(
    spec: DistributionSpec,
    base_dir: Path,
    *,
    builtins: Iterable[Manifest] | None = None,
    tree: Any = None,
    host_version: str | None = None,
) -> DistributionResolution:
    """Select the plugin set and collect every problem (never raises for a bad distribution — ``problems`` says why)."""
    from narranexus.kernel.plugins.builtins import builtin_manifests, slot_tree_with_builtins
    from narranexus.kernel.plugins.compat import host_version as _host_version
    from narranexus.kernel.plugins.manifest import load_manifest
    from narranexus.kernel.plugins.paths import manifest_path

    res = DistributionResolution(spec=spec, base_dir=base_dir)
    host = host_version or _host_version()
    by_id = {m.id: m for m in (builtins if builtins is not None else builtin_manifests())}
    tree = tree or slot_tree_with_builtins()

    try:
        if not Range.parse(spec.engine).contains(host):
            res.problems.append(f"engine: this host is {host}, the distribution wants {spec.engine!r}")
    except ValueError as exc:
        res.problems.append(f"engine: {exc}")

    for pid, ref in spec.plugins.items():
        if ref.bundled:
            plugin_dir = (base_dir / ref.path).resolve()
            mpath = manifest_path(plugin_dir)
            if not mpath.is_file():
                res.problems.append(f"{pid}: bundled path {ref.path!r} has no {mpath.name}")
                continue
            try:
                manifest = load_manifest(mpath, tree=tree, host_version=host)
            except ManifestError as exc:
                res.problems.append(f"{pid}: {exc}")
                continue
            if manifest.id != pid:
                res.problems.append(f"{pid}: bundled manifest declares id {manifest.id!r}")
                continue
            res.picks.append(PluginPick(pid, manifest.version, "path", manifest, path=plugin_dir))
        else:
            manifest = by_id.get(pid)
            if manifest is None:
                res.problems.append(f"{pid}: unknown plugin (not a builtin of this engine; give a path to bundle it)")
                continue
            try:
                if not Range.parse(ref.range).contains(manifest.version):
                    res.problems.append(f"{pid}: engine ships {manifest.version}, the distribution wants {ref.range!r}")
                    continue
            except ValueError as exc:
                res.problems.append(f"{pid}: {exc}")
                continue
            res.picks.append(PluginPick(pid, manifest.version, "builtin", manifest))

    selected = {p.id for p in res.picks}
    selected_manifests = [p.manifest for p in res.picks if p.manifest is not None]
    # Every builtin of this engine must be either selected or excluded: a
    # builtin added later (builtin.prompts was) otherwise silently fell
    # outside every distribution while doctor stayed green.
    unclassified = sorted(pid for pid in by_id if pid not in spec.plugins and pid not in spec.excludes)
    if unclassified:
        res.problems.append(f"unclassified builtins (add to plugins or excludes): {', '.join(unclassified)}")
    excluded = []
    for pid in spec.excludes:
        if pid in by_id:
            excluded.append(pid)
        else:
            res.problems.append(f"excludes: {pid!r} is not a builtin of this engine")
    res.excluded = tuple(excluded)

    for pick in res.picks:
        for dep in pick.manifest.dependencies:
            if dep not in selected:
                where = "excluded" if dep in res.excluded else "not in the distribution"
                res.problems.append(f"{pick.id} depends on {dep}, which is {where}")

    auth = res.pick(spec.auth)
    if auth is None:
        res.problems.append(f"auth: {spec.auth!r} is not in the distribution's plugins")
    elif AUTH_SLOT not in auth.manifest.provides:
        res.problems.append(f"auth: {spec.auth!r} does not provide {AUTH_SLOT}")
    elif not auth.manifest.distribution_only:
        res.problems.append(f"auth: {spec.auth!r} must be distributionOnly to fill {AUTH_SLOT}")

    for path, value in spec.bindings.items():
        # owner / name / owner:name and the +/-/= verbs all resolve to plugin
        # ids through the binding grammar's own helper; only the OWNER half of
        # a value must be a selected plugin (a contribution name is checked at
        # runtime against the registry).
        for provider in sorted(referenced_plugins(value)):
            if provider in selected:
                continue
            if any(provider == c for m in selected_manifests for c in _contribution_names(m)):
                continue
            res.problems.append(f"bindings[{path!r}]: provider {provider!r} is not in the distribution")

    entries: dict[str, str | list[str]] = {p: (v if isinstance(v, str) else list(v)) for p, v in spec.bindings.items()}
    entries[AUTH_SLOT] = spec.auth
    res.bindings = BindingSource(Layer.DISTRIBUTION, entries, origin=spec.id)
    return res


def doctor_report(res: DistributionResolution, *, host_version: str | None = None) -> dict[str, Any]:
    """What ``narranexus dist doctor`` prints: the plugin table, the problems and the size budget."""
    from narranexus.kernel.plugins.compat import host_version as _host_version

    rows = [
        {
            "id": p.id,
            "version": p.version,
            "source": p.source,
            "path": str(p.path) if p.path else "",
            "distributionOnly": p.manifest.distribution_only,
            "quality": p.manifest.quality,
            "backend_deps_mb": p.manifest.size.backend_deps_mb,
            "frontend_kb": p.manifest.size.frontend_kb,
        }
        for p in res.picks
    ]
    return {
        "id": res.spec.id,
        "engine": {"host": host_version or _host_version(), "wanted": res.spec.engine},
        "deployment": res.spec.runtime.deployment,
        "userPlugins": res.spec.runtime.user_plugins,
        "auth": res.spec.auth,
        "targets": list(res.spec.targets),
        "plugins": rows,
        "excluded": list(res.excluded),
        "bindings": {k: v for k, v in res.bindings.entries.items()},
        "size": {
            "backend_deps_mb": round(sum(r["backend_deps_mb"] for r in rows), 2),
            "frontend_kb": round(sum(r["frontend_kb"] for r in rows), 1),
        },
        "problems": list(res.problems),
        "ok": res.ok,
    }


def lock_data(res: DistributionResolution, *, host_version: str | None = None) -> dict[str, Any]:
    """The reproducible plugin set a build bakes in (``narranexus-dist.lock.json``)."""
    from narranexus.kernel.plugins.compat import host_version as _host_version

    return {
        "id": res.spec.id,
        "engine": host_version or _host_version(),
        "plugins": {
            p.id: {"version": p.version, "source": p.source, **({"path": os.path.relpath(p.path, res.base_dir)} if p.path else {})}
            for p in res.picks
        },
        "excluded": list(res.excluded),
        "auth": res.spec.auth,
        "bindings": {k: v for k, v in res.bindings.entries.items()},
        "runtime": {"deployment": res.spec.runtime.deployment, "userPlugins": res.spec.runtime.user_plugins},
    }


def write_lock(res: DistributionResolution, path: Path, *, host_version: str | None = None) -> Path:
    res.raise_for_problems()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(lock_data(res, host_version=host_version), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def resolve_from_env(environ: Mapping[str, str] | None = None, *, host_version: str | None = None) -> DistributionResolution | None:
    """The process's distribution resolved against this engine, or ``None`` when none is configured. Raises on problems: a broken distribution must not boot half-way."""
    path = find_distribution(environ)
    if path is None:
        return None
    spec, base = load_distribution(path)
    res = resolve_distribution(spec, base, host_version=host_version)
    res.raise_for_problems()
    return res


__all__ = [
    "AUTH_SLOT",
    "ENV_DISTRIBUTION",
    "LOCK_FILENAME",
    "DistributionResolution",
    "PluginPick",
    "distribution_file",
    "doctor_report",
    "find_distribution",
    "load_distribution",
    "lock_data",
    "resolve_distribution",
    "resolve_from_env",
    "write_lock",
]
