"""
@file_name: build.py
@author: Bin Liang
@date: 2026-09-04
@description: ``narranexus build``: resolve a distribution, write ``narranexus-dist.lock.json`` and ``builtins.generated.json`` (the plugin set a host bakes in), copy bundled plugins, then run the target packaging — ``wheel`` builds the engine and every selected builtin package with ``uv build``; ``desktop`` runs the Tauri bundle; ``docker`` runs ``docker build`` with the given Dockerfile (the deploy repo owns it). ``--dry-run`` writes the plan and runs nothing.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from narranexus.kernel.plugins.distribution import ENV_DISTRIBUTION, LOCK_FILENAME, DistributionResolution, write_lock

REPO_ROOT = Path(__file__).resolve().parents[3]
GENERATED = "builtins.generated.json"


def package_name(plugin_id: str) -> str:
    """``builtin.frameworks.nexus_power`` → ``narranexus-plugin-frameworks-nexus-power`` (the workspace member name)."""
    return "narranexus-plugin-" + plugin_id.removeprefix("builtin.").replace(".", "-").replace("_", "-")


@dataclass
class BuildResult:
    target: str
    out: Path
    commands: list[list[str]] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    ok: bool = True


def plan(res: DistributionResolution, *, target: str, out: Path, dockerfile: Path | None = None) -> list[list[str]]:
    """The packaging commands for ``target`` (what ``--dry-run`` prints)."""
    if target == "wheel":
        cmds = [["uv", "build", "--package", "narranexus", "--out-dir", str(out / "wheels")]]
        for pick in res.picks:
            if pick.source == "builtin":
                cmds.append(["uv", "build", "--package", package_name(pick.id), "--out-dir", str(out / "wheels")])
            else:
                cmds.append(["uv", "build", str(out / "plugins" / pick.id), "--out-dir", str(out / "wheels")])
        return cmds
    if target == "desktop":
        return [["npm", "run", "tauri", "build", "--prefix", str(REPO_ROOT / "tauri")]]
    if target == "docker":
        tag = f"{res.spec.id.replace('.', '-')}:{res.picks[0].version if res.picks else 'latest'}"
        cmd = ["docker", "build", "-t", tag, "--build-arg", f"{ENV_DISTRIBUTION}={out / LOCK_FILENAME}"]
        if dockerfile is not None:
            cmd += ["-f", str(dockerfile)]
        return [cmd + [str(REPO_ROOT)]]
    raise ValueError(f"unknown target {target!r}")


def build(res: DistributionResolution, *, target: str, out: Path, dry_run: bool = False, dockerfile: Path | None = None) -> BuildResult:
    if target not in res.spec.targets:
        raise ValueError(f"{res.spec.id} does not declare target {target!r} (targets: {list(res.spec.targets)})")
    result = BuildResult(target=target, out=out)
    out.mkdir(parents=True, exist_ok=True)
    lock = write_lock(res, out / LOCK_FILENAME)
    result.log.append(f"lock: {lock}")
    generated = out / GENERATED
    generated.write_text(json.dumps([m.model_dump(by_alias=True, mode="json") for m in res.manifests], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result.log.append(f"builtins: {generated} ({len(res.manifests)} plugins)")
    shutil.copy(res.base_dir / "narranexus-dist.json", out / "narranexus-dist.json")
    for pick in res.picks:
        if pick.path is not None:
            dst = out / "plugins" / pick.id
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(pick.path, dst, ignore=shutil.ignore_patterns("__pycache__", ".venv", "node_modules"))
            result.log.append(f"bundled: {pick.id} -> {dst}")
    if target == "docker" and dockerfile is None and not dry_run:
        result.ok = False
        result.log.append("docker: pass --dockerfile <path> (the deploy repo owns the image definition); nothing built")
        return result
    result.commands = plan(res, target=target, out=out, dockerfile=dockerfile)
    (out / "build-plan.json").write_text(json.dumps({"target": target, "commands": result.commands}, indent=2) + "\n", encoding="utf-8")
    for cmd in result.commands:
        result.log.append(("plan: " if dry_run else "run: ") + " ".join(cmd))
        if dry_run:
            continue
        env = {**os.environ, ENV_DISTRIBUTION: str(out / "narranexus-dist.json")}
        proc = subprocess.run(cmd, cwd=REPO_ROOT, env=env)
        if proc.returncode != 0:
            result.ok = False
            result.log.append(f"failed ({proc.returncode}): {' '.join(cmd)}")
            break
    return result


__all__ = ["GENERATED", "BuildResult", "build", "package_name", "plan"]
