"""
@file_name: distribution_scaffold.py
@author: Bin Liang
@date: 2026-09-04
@description: ``narranexus create-app <id>``: scaffold a distribution repo — narranexus-dist.json (an official distribution's plugin set plus one bundled plugin of your own), the bundled plugin (via the plugin scaffold), branding/, defaults/, a README and a CI workflow running dist doctor + build.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.contracts.distribution import DISTRIBUTION_ID_RE, DISTRIBUTION_FILENAME
from narranexus.kernel.plugins.builtins import builtin_manifests
from narranexus.kernel.plugins.importer import package_name

REPO_ROOT = Path(__file__).resolve().parents[3]
DISTRIBUTIONS_DIR = REPO_ROOT / "distributions"


def _base_plugins(base: str) -> dict[str, str]:
    data = json.loads((DISTRIBUTIONS_DIR / base / DISTRIBUTION_FILENAME).read_text(encoding="utf-8"))
    return {pid: (ref if isinstance(ref, str) else ref.get("range", "*")) for pid, ref in data["plugins"].items() if not isinstance(ref, dict) or "path" not in ref}


def create_app(
    dist_id: str,
    dest: Path,
    *,
    display_name: str = "",
    base: str = "minimal",
    auth: str = "builtin.auth.local",
    deployment: str = "desktop",
) -> list[Path]:
    if not DISTRIBUTION_ID_RE.match(dist_id) or dist_id.startswith("builtin.") or dist_id.startswith("narranexus."):
        raise ValueError(f"invalid distribution id {dist_id!r} (expected <publisher>.<name>, not builtin./narranexus.)")
    if not (DISTRIBUTIONS_DIR / base / DISTRIBUTION_FILENAME).is_file():
        raise ValueError(f"unknown base distribution {base!r}")
    publisher, name = dist_id.split(".", 1)
    display = display_name or name.replace("_", " ").replace("-", " ").title()
    plugin_id = f"{publisher}.{name.replace('-', '_')}_core"
    plugins: dict[str, object] = dict(_base_plugins(base))
    builtin_ids = {m.id for m in builtin_manifests()}
    for pid in ("builtin.auth.local", "builtin.auth.netmind"):
        plugins.pop(pid, None)
    if auth in builtin_ids:
        plugins[auth] = "^1.0"
    plugins[plugin_id] = {"path": f"./plugins/{plugin_id}"}
    if auth not in builtin_ids and auth != plugin_id:
        plugins[auth] = {"path": f"./plugins/{auth}"}
    excludes = sorted(pid for pid in builtin_ids if pid not in plugins)
    spec = {
        "id": dist_id,
        "displayName": display,
        "description": f"{display} — a NarraNexus distribution.",
        "engine": ">=1.15 <2",
        "plugins": plugins,
        "excludes": excludes,
        "branding": {"name": display, "logo": "./branding/logo.svg", "theme": ""},
        "auth": auth,
        "defaults": {"pipelineProfile": "default", "agentSpec": "./defaults/agent.json", "locale": "en"},
        "runtime": {"userPlugins": deployment != "cloud", "deployment": deployment},
        "targets": ["docker"] if deployment == "cloud" else ["desktop", "wheel"],
    }
    written: list[Path] = []

    def w(rel: str, text: str) -> None:
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)

    w(DISTRIBUTION_FILENAME, json.dumps(spec, indent=2) + "\n")
    w("branding/logo.svg", '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#1f6feb"/></svg>\n')
    w("defaults/agent.json", json.dumps({"name": f"{display} Assistant", "description": "", "framework": "nexus_power", "pipelineProfile": "default"}, indent=2) + "\n")
    w(".github/workflows/dist.yml", f"""name: distribution
on: [push, pull_request]
jobs:
  doctor-and-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv pip install --system narranexus
      - run: narranexus dist doctor .
      - run: narranexus build . --target {spec['targets'][0]} --dry-run
""")
    w("README.md", f"""# {display} (`{dist_id}`)

A NarraNexus distribution: `{DISTRIBUTION_FILENAME}` declares the engine range, the plugin set (starting from the official `{base}` distribution), your bundled plugin `{plugin_id}` and the auth provider `{auth}`.

- Check: `narranexus dist doctor .`
- Lock: `narranexus dist lock .`
- Build: `narranexus build . --target {spec['targets'][0]}`
- Run the backend as this distribution: `NARRANEXUS_DIST=$PWD`
- Add plugins: `narranexus plugin new {publisher}.<name> --dir plugins/{publisher}.<name>` and reference it under `plugins` with a `path`.
""")
    if auth not in builtin_ids and auth != plugin_id:
        w(f"plugins/{auth}/narranexus-plugin.json", json.dumps({
            "id": auth, "version": "0.1.0", "displayName": f"{display} auth", "description": "Authentication provider (replace the stub).",
            "minAppVersion": "1.15.0", "hosts": ["backend"], "api": {"auth": 0}, "distributionOnly": True,
            "backend": {"activate": False}, "provides": {"kernel.auth": f"{package_name(auth)}:CONTRIBUTION"},
        }, indent=2) + "\n")
        w(f"plugins/{auth}/backend/__init__.py", '''"""Authentication provider stub — replace ``authenticate`` with your SSO / directory check."""
from __future__ import annotations

from typing import Any, Mapping

from narranexus.kernel.plugins.registry import Contribution


class Provider:
    id = "''' + auth + '''"
    scheme = "bearer"

    async def authenticate(self, request: Any) -> Mapping[str, Any] | None:
        header = request.headers.get("Authorization", "") or ""
        if not header.startswith("Bearer "):
            return None
        return {"user_id": header[7:], "role": "user", "provider": self.id}


CONTRIBUTION = Contribution("auth", lambda: Provider())
''')
    from narranexus.cli.scaffold import scaffold

    written += scaffold(plugin_id, ["hook"], dest / "plugins" / plugin_id, display_name=f"{display} core")
    manifest_path = dest / "plugins" / plugin_id / "narranexus-plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["minAppVersion"] = "1.15.0"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return written


__all__ = ["create_app"]
