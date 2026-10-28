"""
@file_name: main.py
@author: Bin Liang
@date: 2026-09-03
@description: ``narranexus plugin new|link|install|list|enable|disable|uninstall|doctor|bisect|publish-check``, ``narranexus dist doctor|lock``, ``narranexus create-app``, ``narranexus build``, ``narranexus slots|bind|unbind`` and ``narranexus docs gen``.

Every mutating verb goes through the same kernel objects the factory API
uses (``Installer`` / ``RegistryStore`` / ``Bisect``), so the CLI and the
UI can never disagree about state. ``doctor`` is read-only: it validates
every registered plugin against this host and prints the boot decisions
without booting. ``publish-check`` runs the release checklist on a plugin
directory (manifest, versions.json, dist bundle, README, tests) and exits
non-zero on any miss.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Callable

from narranexus.contracts import BindingConflict, ManifestError, PluginError, UnknownEntry

from narranexus.cli.scaffold import TEMPLATES_DIR  # noqa: E402 — one definition of where the templates live
from narranexus.contracts.distribution import is_builtin_id


def _store():
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.paths import registry_path

    return RegistryStore(path=registry_path())


def _print(obj: Any, *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(obj, indent=2, sort_keys=True, default=str))
    elif isinstance(obj, list):
        for row in obj:
            print(row if isinstance(row, str) else json.dumps(row, default=str))
    elif isinstance(obj, dict):
        for k, v in obj.items():
            print(f"{k}: {v}")
    else:
        print(obj)


# ------------------------------------------------------------------ verbs


def cmd_new(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.manifest import PLUGIN_ID_RE

    if not PLUGIN_ID_RE.match(args.id) or is_builtin_id(args.id):
        print(f"invalid plugin id {args.id!r} (expected <publisher>.<name>, not builtin.)", file=sys.stderr)
        return 2
    dest = Path(args.dir or args.id).resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} exists and is not empty", file=sys.stderr)
        return 2
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    missing = [k for k in kinds if not (TEMPLATES_DIR / k).is_dir()]
    if missing:
        print(f"unknown template kind(s) {missing}; available: {sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())}", file=sys.stderr)
        return 2
    from narranexus.cli.scaffold import scaffold

    written = scaffold(args.id, kinds, dest, display_name=args.display_name or args.id.split(".", 1)[1].replace("_", " ").title())
    print(f"created {dest} with {len(written)} files for kinds {kinds}")
    return 0


def cmd_link(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.install import Installer, LocalSource

    # A linked plugin is the developer's own working tree: linking it IS the
    # acknowledgement of whatever permissions it declares (install from a
    # remote source still gates on --yes / `plugin enable --ack`).
    result = Installer(store=_store()).install(LocalSource(Path(args.path), mode="link"), installed_by="cli", replace=args.force, permissions_acknowledged=True)
    print(f"linked {result.plugin_id} {result.version} at {result.path}; restart the app to load it")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.install import Installer

    result = Installer(store=_store()).install(args.source, installed_by="cli", permissions_acknowledged=args.yes, replace=args.force)
    perms = {k: v for k, v in result.permissions.items() if v}
    print(f"installed {result.plugin_id} {result.version} ({result.mode}) at {result.path}")
    if result.warnings:
        print("warnings: " + "; ".join(result.warnings))
    if perms and not args.yes:
        print(f"{result.plugin_id} stays DISABLED until its permissions are acknowledged")
        print(f"declared permissions: {json.dumps(perms)} — acknowledge with `narranexus plugin enable {result.plugin_id} --ack`")
    print("restart the app to load it")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    reg = _store().read()

    def _quality(path: str) -> str:
        try:
            return str(json.loads((Path(path) / "narranexus-plugin.json").read_text(encoding="utf-8")).get("quality", "bronze"))
        except (OSError, ValueError):
            return "?"

    rows = [
        {"id": pid, "version": r.installed_version, "enabled": r.enabled, "state": r.state, "mode": r.mode, "scope": r.scope, "quality": _quality(r.path), "path": r.path, "error": r.last_error}
        for pid, r in sorted(reg.plugins.items())
    ]
    if args.json:
        _print({"plugins": rows, "safe_mode": reg.safe_mode, "safe_mode_reason": reg.safe_mode_reason}, as_json=True)
        return 0
    if reg.safe_mode:
        print(f"SAFE MODE: {reg.safe_mode_reason}")
    if not rows:
        print("no user plugins")
    for r in rows:
        flag = "on " if r["enabled"] else "off"
        print(f"{flag} {r['id']:<32} {r['version']:<10} {r['state']:<14} {r['mode']:<5} {r['scope']:<6} {r['quality']:<7} {r['error'] or ''}")
    return 0


def _toggle_builtin(plugin_id: str, enabled: bool) -> bool:
    """builtin.* ids are toggled through registry.json builtin_overrides; returns False for non-builtins."""
    if not is_builtin_id(plugin_id):
        return False
    from narranexus.kernel.plugins.builtins import check_builtin_toggle

    # Same rules as the factory API (unknown / excluded by the distribution / protected); a refusal
    # is a RegistryError, which main() prints to stderr with a non-zero exit.
    check_builtin_toggle(plugin_id, enabled)

    def _mutate(reg):
        if enabled:
            reg.builtin_overrides.pop(plugin_id, None)
        else:
            reg.builtin_overrides[plugin_id] = {"enabled": False}

    _store().update(_mutate)
    print(f"{plugin_id}: builtin {'enabled' if enabled else 'disabled'}; restart the app")
    return True


def cmd_enable(args: argparse.Namespace) -> int:
    if _toggle_builtin(args.id, True):
        return 0
    from narranexus.kernel.plugins.install.installer import acknowledge_permissions, is_gated

    store = _store()
    current = store.read().plugins.get(args.id)
    if current is None:
        print(f"{args.id} is not installed", file=sys.stderr)
        return 1
    if args.ack:
        rec = acknowledge_permissions(store, args.id)
    elif is_gated(current):
        # The gate is the whole point of the disclosure: enabling without
        # acknowledging would run undisclosed permissions on the next boot.
        print(f"{args.id} declares permissions you have not acknowledged; run `narranexus plugin enable {args.id} --ack`", file=sys.stderr)
        return 2
    else:
        rec = store.set_enabled(args.id, True)
    print(f"{args.id}: enabled (state {rec.state}); restart the app")
    return 0


def cmd_disable(args: argparse.Namespace) -> int:
    if _toggle_builtin(args.id, False):
        return 0
    rec = _store().set_enabled(args.id, False)
    print(f"{args.id}: disabled (state {rec.state}); restart the app")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.install import Installer

    Installer(store=_store()).uninstall(args.id, purge_files=not args.keep_files)
    print(f"{args.id}: uninstalled")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.compat import host_version
    from narranexus.kernel.plugins.loader import discover, plan_load

    store = _store()
    found = discover(cloud=False, user_registry_path=store.path, host_version=host_version())
    users = [m for m in found.manifests if not m.is_builtin]
    plan = plan_load(found.manifests)
    report = {
        "host_version": host_version(),
        "safe_mode": found.safe_mode,
        "would_load": [m.id for m in plan.ordered if not m.is_builtin],
        "blocked": plan.blocked,
        "rejected": found.rejected,
        "builtins": [m.id for m in found.manifests if m.is_builtin],
    }
    _print(report, as_json=args.json)
    # Non-zero whenever anything was rejected or blocked — a plugin author's
    # CI must go red on a rejected plugin (the old `and not users` clause
    # answered 0 with four of five plugins rejected).
    return 1 if (found.rejected or plan.blocked) else 0


def cmd_bisect(args: argparse.Namespace) -> int:
    from narranexus.kernel.plugins.bisect import Bisect

    b = Bisect(_store())
    if args.action == "start":
        step = b.start()
    elif args.action == "good":
        step = b.answer(good=True)
    elif args.action == "bad":
        step = b.answer(good=False)
    else:
        b.stop()
        print("bisect stopped; cleared plugins re-enabled")
        return 0
    if step.culprit:
        print(f"culprit: {step.culprit} (kept disabled) — run `narranexus plugin bisect stop`")
    else:
        print(f"{step.remaining} suspects; enabled for the next start: {', '.join(step.trial) or '-'}; restart, then answer good|bad")
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    reg = _store().rollback_to_lkg()
    print(f"registry restored from last-known-good ({len(reg.plugins)} plugins); restart the app")
    return 0


def cmd_publish_check(args: argparse.Namespace) -> int:
    from narranexus.cli.publish_check import publish_check

    problems = publish_check(Path(args.path))
    for p in problems:
        print(f"- {p}")
    print("ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


def cmd_docs_gen(args: argparse.Namespace) -> int:
    import subprocess

    root = Path(__file__).resolve().parents[3]
    rc = 0
    for script in ("scripts/dev/gen_plugin_docs.py", "scripts/dev/gen_theme_tokens.py"):
        cmd = [sys.executable, str(root / script)] + (["--write"] if args.write else [])
        rc |= subprocess.call(cmd)
    return rc


# ------------------------------------------------------------------ parser


def _distribution(args: argparse.Namespace):
    from narranexus.kernel.plugins.distribution import load_distribution, resolve_distribution

    spec, base = load_distribution(Path(args.path))
    return resolve_distribution(spec, base)


def cmd_dist_doctor(args: argparse.Namespace) -> int:
    """Resolve a distribution against this engine and print the report; non-zero on any problem."""
    from narranexus.kernel.plugins.distribution import doctor_report

    report = doctor_report(_distribution(args))
    if args.json:
        _print(report, as_json=True)
    else:
        print(f"{report['id']}: engine {report['engine']['host']} (wants {report['engine']['wanted']}), "
              f"{report['deployment']}, auth={report['auth']}, userPlugins={report['userPlugins']}")
        for row in report["plugins"]:
            flag = " distributionOnly" if row["distributionOnly"] else ""
            where = f" ({row['path']})" if row["path"] else ""
            print(f"  {row['id']} {row['version']} [{row['source']}{flag}]{where}")
        if report["excluded"]:
            print(f"  excluded: {', '.join(report['excluded'])}")
        print(f"  size: backend deps {report['size']['backend_deps_mb']} MB, frontend {report['size']['frontend_kb']} kB")
        for problem in report["problems"]:
            print(f"  PROBLEM: {problem}")
        print("ok" if report["ok"] else f"{len(report['problems'])} problem(s)")
    return 0 if report["ok"] else 1


def cmd_dist_lock(args: argparse.Namespace) -> int:
    """Write the resolved plugin set next to the declaration (or at --out)."""
    from narranexus.kernel.plugins.distribution import LOCK_FILENAME, write_lock

    res = _distribution(args)
    out = Path(args.out) if args.out else res.base_dir / LOCK_FILENAME
    path = write_lock(res, out)
    print(str(path))
    return 0


def cmd_create_app(args: argparse.Namespace) -> int:
    """Scaffold a distribution repo: narranexus-dist.json, one bundled plugin, branding/, defaults/, CI."""
    from narranexus.cli.distribution_scaffold import create_app

    dest = Path(args.dir or args.id.split(".", 1)[1]).resolve()
    if dest.exists() and any(dest.iterdir()):
        print(f"{dest} exists and is not empty", file=sys.stderr)
        return 2
    try:
        written = create_app(args.id, dest, display_name=args.display_name, base=args.base, auth=args.auth, deployment=args.deployment)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"created {dest} with {len(written)} files; next: narranexus dist doctor {dest}")
    return 0


def cmd_build(args: argparse.Namespace) -> int:
    """Resolve a distribution, write its lock + generated builtins list, and run (or print) the target build."""
    from narranexus.cli.build import build

    res = _distribution(args)
    if not res.ok:
        for problem in res.problems:
            print(f"PROBLEM: {problem}", file=sys.stderr)
        return 1
    out = Path(args.out) if args.out else Path("build") / res.spec.id
    result = build(res, target=args.target, out=out, dry_run=args.dry_run, dockerfile=Path(args.dockerfile) if args.dockerfile else None)
    for line in result.log:
        print(line)
    return 0 if result.ok else 1


def cmd_slots(args: argparse.Namespace) -> int:
    """Every slot by domain: contract, candidates registered here, and what is bound from which layer."""
    from narranexus.cli.bindings_cli import booted_registries, render_catalog
    from narranexus.kernel.plugins.catalog import slot_catalog, toml_template

    catalog = slot_catalog(booted_registries(), domain=args.domain or None)
    if args.toml_template:
        print(toml_template(catalog), end="")
    elif args.json:
        _print(catalog, as_json=True)
    else:
        print(render_catalog(catalog))
    return 0


def cmd_bind(args: argparse.Namespace) -> int:
    """Bind a slot in <plugin home>/narranexus.toml (restart to apply)."""
    from narranexus.cli.bindings_cli import bind, booted_registries
    from narranexus.platform.bindings_runtime import config_path

    result = bind(booted_registries(), config_path(), args.slot, args.provider)
    print(f"{result['slot']} = {result['bound']} → {result['file']} (restart the app to apply)")
    return 0


def cmd_unbind(args: argparse.Namespace) -> int:
    from narranexus.cli.bindings_cli import unbind
    from narranexus.platform.bindings_runtime import config_path

    print(f"{args.slot}: {'removed' if unbind(config_path(), args.slot) else 'was not bound'} (restart the app to apply)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="narranexus", description="NarraNexus plugin tools")
    sub = parser.add_subparsers(dest="command", required=True)

    plugin = sub.add_parser("plugin", help="manage user plugins")
    psub = plugin.add_subparsers(dest="verb", required=True)

    p = psub.add_parser("new", help="scaffold a plugin from templates")
    p.add_argument("id")
    p.add_argument("--kinds", default="routes", help="comma-separated template kinds")
    p.add_argument("--dir", default=None)
    p.add_argument("--display-name", default=None)
    p.set_defaults(fn=cmd_new)

    p = psub.add_parser("link", help="register a local plugin directory in place")
    p.add_argument("path")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_link)

    p = psub.add_parser("install", help="install from owner/repo[@tag], owner/repo#ref, or a path")
    p.add_argument("source")
    p.add_argument("--yes", action="store_true", help="acknowledge declared permissions")
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_install)

    p = psub.add_parser("list")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = psub.add_parser("enable")
    p.add_argument("id")
    p.add_argument("--ack", action="store_true", help="acknowledge declared permissions")
    p.set_defaults(fn=cmd_enable)

    p = psub.add_parser("disable")
    p.add_argument("id")
    p.set_defaults(fn=cmd_disable)

    p = psub.add_parser("uninstall")
    p.add_argument("id")
    p.add_argument("--keep-files", action="store_true")
    p.set_defaults(fn=cmd_uninstall)

    p = psub.add_parser("doctor", help="validate every registered plugin against this host")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_doctor)

    p = psub.add_parser("bisect")
    p.add_argument("action", choices=["start", "good", "bad", "stop"])
    p.set_defaults(fn=cmd_bisect)

    p = psub.add_parser("rollback", help="restore registry.json from last-known-good")
    p.set_defaults(fn=cmd_rollback)

    p = psub.add_parser("publish-check", help="release checklist for a plugin directory")
    p.add_argument("path", nargs="?", default=".")
    p.set_defaults(fn=cmd_publish_check)

    dist = sub.add_parser("dist", help="distributions (narranexus-dist.json)")
    dsub_ = dist.add_subparsers(dest="verb", required=True)
    p = dsub_.add_parser("doctor", help="resolve a distribution against this engine and report problems")
    p.add_argument("path", nargs="?", default=".", help="narranexus-dist.json or its directory")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_dist_doctor)
    p = dsub_.add_parser("lock", help="write narranexus-dist.lock.json with the resolved plugin set")
    p.add_argument("path", nargs="?", default=".")
    p.add_argument("--out", default="")
    p.set_defaults(fn=cmd_dist_lock)

    p = sub.add_parser("create-app", help="scaffold a distribution repo (narranexus-dist.json + a bundled plugin)")
    p.add_argument("id", help="distribution id, <publisher>.<name>")
    p.add_argument("--dir", default="", help="destination (default: ./<name>)")
    p.add_argument("--display-name", default="")
    p.add_argument("--base", default="minimal", choices=["minimal", "desktop", "cloud"], help="official distribution whose plugin set to start from")
    p.add_argument("--auth", default="builtin.auth.local", help="auth provider plugin id (builtin.auth.local | builtin.auth.netmind | your own)")
    p.add_argument("--deployment", default="desktop", choices=["desktop", "cloud", "headless"])
    p.set_defaults(fn=cmd_create_app)

    p = sub.add_parser("build", help="build a distribution: lock + generated builtins list + target packaging")
    p.add_argument("path", nargs="?", default=".", help="narranexus-dist.json or its directory")
    p.add_argument("--target", required=True, choices=["desktop", "docker", "wheel"])
    p.add_argument("--out", default="", help="output directory (default build/<dist id>)")
    p.add_argument("--dockerfile", default="", help="docker target: the Dockerfile to build with (the deploy repo's)")
    p.add_argument("--dry-run", action="store_true", help="write the lock/plan but run no packaging command")
    p.set_defaults(fn=cmd_build)

    p = sub.add_parser("slots", help="the slot catalog: what can be replaced, the candidates, and what is bound")
    p.add_argument("--domain", default="", help="only this domain (kernel, prompt, turn, model, agent, ingress, backend, content, ui)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--toml-template", action="store_true", help="print a commented narranexus.toml with every slot")
    p.set_defaults(fn=cmd_slots)
    p = sub.add_parser("bind", help="bind a slot in narranexus.toml: narranexus bind prompt.assembler acme.brand")
    p.add_argument("slot")
    p.add_argument("provider", nargs="+", help="plugin id, contribution name or owner:name; several for a many-arity slot (order matters)")
    p.set_defaults(fn=cmd_bind)
    p = sub.add_parser("unbind", help="remove a slot binding from narranexus.toml")
    p.add_argument("slot")
    p.set_defaults(fn=cmd_unbind)

    docs = sub.add_parser("docs", help="generated documentation")
    dsub = docs.add_subparsers(dest="verb", required=True)
    p = dsub.add_parser("gen")
    p.add_argument("--write", action="store_true")
    p.set_defaults(fn=cmd_docs_gen)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    fn: Callable[[argparse.Namespace], int] = args.fn
    try:
        return fn(args)
    except (ManifestError, FileNotFoundError, KeyError, PermissionError, BindingConflict, UnknownEntry, PluginError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — a CLI prints, it does not traceback
        print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
