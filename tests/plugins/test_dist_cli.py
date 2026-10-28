"""
@file_name: test_dist_cli.py
@author: Bin Liang
@date: 2026-09-04
@description: `narranexus dist doctor` prints the resolution and exits non-zero on problems; `narranexus dist lock` writes the lock file.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.cli.main import main

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _forget_bundled_packages():
    """Bundled plugins get a synthetic ``nxplugins.<id>`` package; each test must leave the process clean."""
    from narranexus.kernel.plugins.importer import uninstall_synthetic_package

    yield
    for pid in ("acme.crm", "acme.auth-sso", "acme.sso"):
        uninstall_synthetic_package(pid)


def test_doctor_reports_and_exit_codes(tmp_path: Path, capsys):
    assert main(["dist", "doctor", str(REPO / "distributions" / "minimal")]) == 0
    out = capsys.readouterr().out
    assert "narranexus.minimal" in out and out.strip().endswith("ok")
    assert main(["dist", "doctor", str(REPO / "distributions" / "cloud"), "--json"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] and report["auth"] == "builtin.auth.netmind"
    (tmp_path / "narranexus-dist.json").write_text(json.dumps({"id": "acme.bad", "displayName": "b", "plugins": {"ghost.x": "*"}}))
    assert main(["dist", "doctor", str(tmp_path)]) == 1
    assert "PROBLEM: ghost.x: unknown plugin" in capsys.readouterr().out
    assert main(["dist", "doctor", str(tmp_path / "missing")]) == 1
    assert "not found" in capsys.readouterr().err


def test_lock_writes_next_to_the_declaration(tmp_path: Path, capsys):
    src = REPO / "distributions" / "example-tob"
    out = tmp_path / "lock.json"
    assert main(["dist", "lock", str(src), "--out", str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(out)
    assert json.loads(out.read_text())["auth"] == "acme.auth-sso"


def test_create_app_scaffolds_a_distribution_that_doctor_accepts(tmp_path: Path, capsys):
    dest = tmp_path / "crm"
    assert main(["create-app", "acme.crm", "--dir", str(dest), "--auth", "acme.sso", "--deployment", "cloud"]) == 0
    spec = json.loads((dest / "narranexus-dist.json").read_text())
    assert spec["id"] == "acme.crm" and spec["auth"] == "acme.sso" and spec["runtime"] == {"userPlugins": False, "deployment": "cloud"}
    assert spec["plugins"]["acme.crm_core"] == {"path": "./plugins/acme.crm_core"} and spec["plugins"]["acme.sso"] == {"path": "./plugins/acme.sso"}
    assert "builtin.auth.local" not in spec["plugins"] and "builtin.chat" in spec["plugins"]
    assert (dest / "plugins" / "acme.sso" / "backend" / "__init__.py").is_file() and (dest / ".github" / "workflows" / "dist.yml").is_file()
    capsys.readouterr()
    assert main(["dist", "doctor", str(dest)]) == 0, capsys.readouterr().out
    assert main(["create-app", "acme.crm", "--dir", str(dest)]) == 2  # not empty
    assert main(["create-app", "builtin.x", "--dir", str(tmp_path / "b")]) == 2


def test_build_dry_run_writes_lock_generated_list_and_plan(tmp_path: Path, capsys):
    out = tmp_path / "out"
    assert main(["build", str(REPO / "distributions" / "example-tob"), "--target", "docker", "--out", str(out), "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert (out / "narranexus-dist.lock.json").is_file() and (out / "build-plan.json").is_file()
    generated = json.loads((out / "builtins.generated.json").read_text())
    assert {m["id"] for m in generated} >= {"acme.crm", "acme.auth-sso", "builtin.chat"}
    assert (out / "plugins" / "acme.crm" / "narranexus-plugin.json").is_file()
    assert "plan: docker build" in text and "NARRANEXUS_DIST=" in text
    # a target the distribution does not declare is refused
    assert main(["build", str(REPO / "distributions" / "example-tob"), "--target", "wheel", "--out", str(out), "--dry-run"]) == 1
    assert "does not declare target" in capsys.readouterr().err
    # wheel plan names the engine and one package per selected builtin
    out2 = tmp_path / "out2"
    assert main(["build", str(REPO / "distributions" / "minimal"), "--target", "wheel", "--out", str(out2), "--dry-run"]) == 0
    plan = json.loads((out2 / "build-plan.json").read_text())["commands"]
    assert plan[0][:4] == ["uv", "build", "--package", "narranexus"] and any("narranexus-plugin-frameworks-nexus-power" in c for c in plan)
    # docker without a Dockerfile builds nothing for real
    assert main(["build", str(REPO / "distributions" / "example-tob"), "--target", "docker", "--out", str(tmp_path / "out3")]) == 1
