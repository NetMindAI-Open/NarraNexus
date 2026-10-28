"""
@file_name: test_desktop_netmind_env_guard.py
@date: 2026-09-09
@description: The desktop build refuses to bake protago-dev NetMind endpoints.

B-40: a local "Sign in with GitHub" opened "Netmind AI Test by protagohhz" and
redirected to accounts.protago-dev.com. Every empty VITE_NETMIND_* in a build
used to fall back to compiled-in protago-dev defaults with a green build, so a
lost repo Variable would ship the dev OAuth app. scripts/release/
check_desktop_netmind_env.sh is the gate build-desktop.sh runs before building
(env) and after the frontend build (bundle); these tests run the real script.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GUARD = REPO / "scripts" / "release" / "check_desktop_netmind_env.sh"
BUILD = REPO / "scripts" / "release" / "build-desktop.sh"
WORKFLOW = REPO / ".github" / "workflows" / "build-desktop.yml"

PROD_ENV = {
    "VITE_ENABLE_POWER_LOGIN": "true",
    "NARRANEXUS_ENABLE_POWER_LOGIN": "true",
    "VITE_NETMIND_AUTH_API": "https://auth-api.netmind.ai",
    "VITE_NETMIND_ACCOUNTS_URL": "https://accounts.netmind.ai",
    "VITE_NETMIND_SYS_CODE": "f925fc2c",
    "VITE_NETMIND_REGISTER_URL": "https://www.netmind.ai/sign/register",
    "NETMIND_AUTH_API_URL": "https://auth-api.netmind.ai",
    "BILLING_API_BASE": "https://billing.api.netmind.ai",
    "NETMIND_KEY_API_BASE": "https://inference.api.netmind.ai",
    "NETMIND_INFERENCE_BASE": "https://api.netmind.ai/inference-api",
}


def _run(*args: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    # A clean environment: nothing from the developer's shell may leak in.
    return subprocess.run(
        ["bash", str(GUARD), *args],
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env},
        capture_output=True,
        text=True,
        check=False,
    )


class TestEnvGate:
    def test_full_prod_set_passes(self):
        r = _run("env", env=PROD_ENV)
        assert r.returncode == 0, r.stderr
        assert "accounts=https://accounts.netmind.ai" in r.stdout

    def test_community_build_without_power_login_passes(self):
        r = _run("env", env={})
        assert r.returncode == 0, r.stderr
        assert "Power login OFF" in r.stdout

    def test_release_requires_power_login(self):
        r = _run("env", env={"NARRANEXUS_REQUIRE_POWER_LOGIN": "true"})
        assert r.returncode != 0
        assert "NARRANEXUS_REQUIRE_POWER_LOGIN" in r.stderr

    def test_release_with_prod_set_passes(self):
        r = _run("env", env={**PROD_ENV, "NARRANEXUS_REQUIRE_POWER_LOGIN": "true"})
        assert r.returncode == 0, r.stderr

    @pytest.mark.parametrize("missing", sorted(PROD_ENV.keys() - {
        "VITE_ENABLE_POWER_LOGIN", "NARRANEXUS_ENABLE_POWER_LOGIN"}))
    def test_each_missing_endpoint_fails(self, missing):
        env = {k: v for k, v in PROD_ENV.items() if k != missing}
        r = _run("env", env=env)
        assert r.returncode != 0
        assert missing in r.stderr

    @pytest.mark.parametrize(
        ("var", "value"),
        [
            ("VITE_NETMIND_ACCOUNTS_URL", "https://accounts.protago-dev.com"),
            ("VITE_NETMIND_AUTH_API", "https://userauth.protago-dev.com"),
            ("NETMIND_INFERENCE_BASE", "https://test.api.netmind.ai/inference-api"),
            ("BILLING_API_BASE", "http://billing.api.netmind.ai"),
            ("NETMIND_KEY_API_BASE", "https://netmind.ai.evil.example"),
        ],
    )
    def test_non_prod_endpoint_fails(self, var, value):
        r = _run("env", env={**PROD_ENV, var: value})
        assert r.returncode != 0
        assert var in r.stderr

    def test_frontend_and_backend_flags_must_agree(self):
        r = _run("env", env={**PROD_ENV, "NARRANEXUS_ENABLE_POWER_LOGIN": ""})
        assert r.returncode != 0
        assert "disagree" in r.stderr

    def test_frontend_and_backend_auth_must_match(self):
        r = _run(
            "env",
            env={**PROD_ENV, "NETMIND_AUTH_API_URL": "https://other-auth.netmind.ai"},
        )
        assert r.returncode != 0
        assert "different auth services" in r.stderr


class TestBundleGate:
    def test_dev_endpoint_in_bundle_fails(self, tmp_path):
        (tmp_path / "assets").mkdir()
        (tmp_path / "assets" / "index.js").write_text(
            'const a="https://accounts.protago-dev.com";'
        )
        r = _run("bundle", str(tmp_path), env={})
        assert r.returncode != 0
        assert "index.js" in r.stderr

    def test_clean_bundle_passes(self, tmp_path):
        (tmp_path / "index.js").write_text('const a="https://accounts.netmind.ai";')
        r = _run("bundle", str(tmp_path), env={})
        assert r.returncode == 0, r.stderr

    def test_missing_dist_fails(self, tmp_path):
        r = _run("bundle", str(tmp_path / "nope"), env={})
        assert r.returncode != 0


class TestWiring:
    def test_build_desktop_runs_env_gate_before_frontend_build(self):
        text = BUILD.read_text()
        gate = text.index('check_desktop_netmind_env.sh" env')
        build = text.index("npm run build")
        bundle = text.index('check_desktop_netmind_env.sh" bundle')
        assert gate < build < bundle

    def test_workflow_requires_power_login_on_tags(self):
        text = WORKFLOW.read_text()
        assert (
            "NARRANEXUS_REQUIRE_POWER_LOGIN: ${{ startsWith(github.ref, "
            "'refs/tags/') && 'true' || '' }}"
        ) in text
        # On the same step that runs the build (where the VITE_* env lives).
        step = text[text.index("run: bash scripts/release/build-desktop.sh"):]
        step = step[: step.index("\n      - name:")]
        assert "NARRANEXUS_REQUIRE_POWER_LOGIN" in step
        assert "VITE_NETMIND_ACCOUNTS_URL" in step
