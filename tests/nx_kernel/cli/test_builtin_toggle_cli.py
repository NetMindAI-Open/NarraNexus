"""
@file_name: test_builtin_toggle_cli.py
@author: Bin Liang
@date: 2026-10-14
@description: `narranexus plugin enable|disable <builtin>` applies the same rules as the factory API: unknown / protected / distribution-excluded builtins are refused (non-zero, stderr), a kept builtin still toggles through registry.json builtin_overrides.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus.cli.main import main as cli
from narranexus.contracts.distribution import DistributionError
from narranexus.kernel.plugins.builtins import check_builtin_toggle, excluded_builtins_of_running_distribution
from narranexus.kernel.plugins.distribution import ENV_DISTRIBUTION
from narranexus.kernel.plugins.lifecycle import RegistryError
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

DISTRIBUTIONS = Path(__file__).resolve().parents[3] / "distributions"


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(tmp_path))
    monkeypatch.delenv(ENV_DISTRIBUTION, raising=False)
    return tmp_path


def _overrides(home: Path) -> dict:
    f = home / "registry.json"
    return json.loads(f.read_text()).get("builtin_overrides", {}) if f.exists() else {}


def test_cli_refuses_a_builtin_the_running_distribution_excludes(home: Path, monkeypatch, capsys):
    monkeypatch.setenv(ENV_DISTRIBUTION, str(DISTRIBUTIONS / "minimal"))  # excludes builtin.browser
    for verb in ("enable", "disable"):
        assert cli(["plugin", verb, "builtin.browser"]) != 0
        err = capsys.readouterr().err
        assert "excluded" in err
        # A plugin-rule refusal is a user-facing answer, not a crash: no exception class name.
        assert err.startswith("error: builtin.browser") and "RegistryError" not in err
    assert _overrides(home) == {}


def test_cli_still_toggles_a_builtin_the_distribution_keeps(home: Path, monkeypatch, capsys):
    monkeypatch.setenv(ENV_DISTRIBUTION, str(DISTRIBUTIONS / "minimal"))
    assert cli(["plugin", "disable", "builtin.chat"]) == 0
    assert _overrides(home) == {"builtin.chat": {"enabled": False}}
    assert cli(["plugin", "enable", "builtin.chat"]) == 0
    assert _overrides(home) == {}


def test_cli_without_a_distribution_toggles_the_browser(home: Path, capsys):
    assert cli(["plugin", "disable", "builtin.browser"]) == 0
    assert _overrides(home) == {"builtin.browser": {"enabled": False}}


def test_the_shared_check_is_the_one_place_the_rules_live():
    with pytest.raises(KeyError):
        check_builtin_toggle("builtin.nope", True, excluded=())
    with pytest.raises(RegistryError, match="protected"):
        check_builtin_toggle("builtin.nexus_plugins_module", False, excluded=())
    with pytest.raises(RegistryError, match="excluded"):
        check_builtin_toggle("builtin.browser", True, excluded={"builtin.browser"})
    # excluded wins over protected, in both directions (enabling a protected builtin is otherwise fine)
    with pytest.raises(RegistryError, match="excluded"):
        check_builtin_toggle("builtin.nexus_plugins_module", True, excluded={"builtin.nexus_plugins_module"})
    assert check_builtin_toggle("builtin.teams", False, excluded={"builtin.browser"}).id == "builtin.teams"


def test_excluded_set_is_resolved_from_the_environment(monkeypatch):
    monkeypatch.delenv(ENV_DISTRIBUTION, raising=False)
    assert excluded_builtins_of_running_distribution() == frozenset()
    monkeypatch.setenv(ENV_DISTRIBUTION, str(DISTRIBUTIONS / "cloud"))
    assert excluded_builtins_of_running_distribution() >= {"builtin.browser", "builtin.auth.local"}


def test_a_broken_distribution_is_an_error_not_a_silent_empty_set(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(ENV_DISTRIBUTION, str(tmp_path / "missing"))
    with pytest.raises(DistributionError):
        excluded_builtins_of_running_distribution()
