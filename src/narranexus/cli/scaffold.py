"""
@file_name: scaffold.py
@author: Bin Liang
@date: 2026-09-03
@description: Compose a plugin directory from one or more kind templates (``templates/<kind>/``).

Each template contributes files under ``backend/``, ``frontend/``,
``tests/`` and a fragment of the manifest (``manifest.fragment.json``).
Fragments are deep-merged into one ``narranexus-plugin.json`` (lists
concatenate, dicts merge, scalars last-wins); every file has ``__PLUGIN_ID__``,
``__PLUGIN_PKG__`` (id with dots as underscores), ``__DISPLAY_NAME__`` and
``__TABLE_PREFIX__`` substituted.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from narranexus.contracts.table import table_prefix_for

PLACEHOLDERS = ("__PLUGIN_ID__", "__PLUGIN_PKG__", "__DISPLAY_NAME__", "__TABLE_PREFIX__")


def resolve_templates_dir() -> Path:
    """Where the scaffold templates are.

    A wheel carries them as package data (``narranexus/cli/resources/templates``,
    hatch ``force-include`` of the repo's ``templates/``); a source checkout /
    editable install reads the repo's ``templates/`` directly. Anything else is
    an installation error, said plainly instead of a FileNotFoundError deep in
    ``iterdir``.
    """
    packaged = Path(__file__).resolve().parent / "resources" / "templates"
    if packaged.is_dir():
        return packaged
    checkout = Path(__file__).resolve().parents[3] / "templates"
    if checkout.is_dir():
        return checkout
    raise RuntimeError(
        "narranexus scaffold templates not found: neither the packaged copy "
        f"({packaged}) nor a source checkout ({checkout}) exists — reinstall narranexus"
    )


TEMPLATES_DIR = resolve_templates_dir()


def _merge(a: Any, b: Any) -> Any:
    if isinstance(a, dict) and isinstance(b, dict):
        out = dict(a)
        for k, v in b.items():
            out[k] = _merge(a[k], v) if k in a else v
        return out
    if isinstance(a, list) and isinstance(b, list):
        return a + [x for x in b if x not in a]
    return b


def substitute(text: str, plugin_id: str, display_name: str) -> str:
    pkg = plugin_id.replace(".", "_").replace("-", "_")
    return (
        text.replace("__PLUGIN_ID__", plugin_id)
        .replace("__PLUGIN_PKG__", pkg)
        .replace("__DISPLAY_NAME__", display_name)
        .replace("__TABLE_PREFIX__", table_prefix_for(plugin_id))
    )


# Byte-code and test-run leftovers a template directory may accumulate when its
# own tests are executed in place; they are never part of a scaffolded plugin.
_ARTIFACT_DIRS = frozenset({"__pycache__", ".test-home", ".pytest_cache"})
_ARTIFACT_FILES = frozenset({".test-report.json", ".plugin-changelog.jsonl"})


def _is_artifact(rel: Path) -> bool:
    return any(part in _ARTIFACT_DIRS for part in rel.parts) or rel.name in _ARTIFACT_FILES or rel.suffix == ".pyc"


PLUGIN_CI_WORKFLOW = """name: plugin
on:
  push:
    branches: [main]
    tags: ["*"]
  pull_request:
jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - run: uv venv && uv pip install narranexus pytest pytest-asyncio
      - run: uv run pytest tests -q
      - run: uv run narranexus plugin publish-check .
  release:
    # A tag equal to the manifest version publishes the release assets a host installs from
    # (owner/repo@<version>): the manifest, backend.zip, versions.json and the built frontend bundle.
    if: startsWith(github.ref, 'refs/tags/')
    needs: check
    runs-on: ubuntu-latest
    permissions:
      contents: write
    steps:
      - uses: actions/checkout@v4
      - run: |
          test "$(python3 -c 'import json;print(json.load(open("narranexus-plugin.json"))["version"])')" = "${GITHUB_REF_NAME}"
          zip -r backend.zip backend
      - uses: softprops/action-gh-release@v2
        with:
          files: |
            narranexus-plugin.json
            backend.zip
            versions.json
            frontend/dist/plugin.js
            frontend/dist/styles.css
          fail_on_unmatched_files: false
"""


def scaffold(plugin_id: str, kinds: list[str], dest: Path, *, display_name: str, templates_dir: Path = TEMPLATES_DIR) -> list[Path]:
    manifest: dict[str, Any] = {
        "id": plugin_id,
        "version": "0.1.0",
        "displayName": display_name,
        "description": "",
        "license": "MIT",
        "minAppVersion": "0.0.0",
        "hosts": ["backend"],
        "backend": {"package": "backend", "pip": [], "activate": True},
        "quality": "bronze",
    }
    written: list[Path] = []
    init_parts: list[tuple[str, str]] = []  # (kind, template body)
    owners: dict[Path, str] = {}  # target path -> kind that wrote it
    for kind in kinds:
        src = templates_dir / kind
        fragment = src / "manifest.fragment.json"
        if fragment.is_file():
            manifest = _merge(manifest, json.loads(substitute(fragment.read_text(encoding="utf-8"), plugin_id, display_name)))
        for path in sorted(src.rglob("*")):
            if not path.is_file() or path.name == "manifest.fragment.json" or _is_artifact(path.relative_to(src)):
                continue
            rel = path.relative_to(src)
            if rel.parts[0] == "backend" and rel.name == "__init__.py":
                init_parts.append((kind, substitute(path.read_text(encoding="utf-8"), plugin_id, display_name)))
                continue
            target = dest / Path(substitute(str(rel), plugin_id, display_name))
            if target in owners:
                # Two kinds writing the same file (ui_page + ui_panel both own
                # frontend/src/index.ts): the second would silently overwrite
                # the first while the merged manifest declared both — an empty
                # route with no error. Refuse with the exact conflict.
                raise ValueError(
                    f"kinds {owners[target]!r} and {kind!r} both generate {rel}: scaffold one UI kind and add the other's "
                    f"registration to {rel} by hand"
                )
            owners[target] = kind
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(substitute(path.read_text(encoding="utf-8"), plugin_id, display_name), encoding="utf-8")
            written.append(target)
    backend = dest / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    init = backend / "__init__.py"
    header = (
        f'"""{display_name} — a NarraNexus plugin ({plugin_id}).\n\n'
        "Contributions are module-level constants named in narranexus-plugin.json;\n"
        "activate(ctx) runs on the plugin's first activation event.\n"
        '"""\nfrom __future__ import annotations\n\n'
    )
    # Each kind's template defines its own activate(ctx); N kinds would have
    # produced N same-named functions with only the last one alive (ruff F811,
    # and the one at the top — the one a developer edits first — dead). Each
    # becomes _activate_<kind>(ctx) and one activate(ctx) calls them all.
    if not init_parts:
        body = "def activate(ctx):\n    ctx.log.info('activated')\n"
    elif len(init_parts) == 1:
        body = init_parts[0][1]
    else:
        renamed = [re.sub(r"^def activate\(", f"def _activate_{kind}(", text, count=1, flags=re.M) for kind, text in init_parts]
        calls = "\n".join(f"    _activate_{kind}(ctx)" for kind, _ in init_parts)
        body = "\n\n".join(renamed) + "\n\n\ndef activate(ctx):\n    \"\"\"Runs every kind's activation in scaffold order.\"\"\"\n" + calls + "\n"
    init.write_text(header + body + "\n", encoding="utf-8")
    written.append(init)
    mpath = dest / "narranexus-plugin.json"
    mpath.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    written.append(mpath)
    readme = dest / "README.md"
    if not readme.exists():
        readme.write_text(
            f"# {display_name}\n\nA NarraNexus plugin (`{plugin_id}`).\n\n"
            "```bash\nnarranexus plugin link .   # register in place, then restart the app\nuv run pytest tests -q\n"
            "narranexus plugin publish-check .\n```\n",
            encoding="utf-8",
        )
        written.append(readme)
    changelog = dest / "CHANGELOG.md"
    if not changelog.exists():
        changelog.write_text("# Changelog\n\n## 0.1.0\n\n- Initial scaffold.\n", encoding="utf-8")
        written.append(changelog)
    versions = dest / "versions.json"
    if not versions.exists():
        versions.write_text(json.dumps({"0.1.0": manifest["minAppVersion"]}, indent=2) + "\n", encoding="utf-8")
        written.append(versions)
    (dest / "tests").mkdir(exist_ok=True)
    workflow = dest / ".github" / "workflows" / "plugin-ci.yml"
    workflow.parent.mkdir(parents=True, exist_ok=True)
    workflow.write_text(PLUGIN_CI_WORKFLOW, encoding="utf-8")
    written.append(workflow)

    return written


__all__ = ["PLACEHOLDERS", "TEMPLATES_DIR", "resolve_templates_dir", "scaffold", "substitute"]
