from pathlib import Path

from narranexus.sdk import Stage
from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_profile_registers_and_names_its_strategies(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        (name,) = host.names("turn.profiles")
        profile = host.registry("turn.profiles").get(name)
        assert profile.id == name
        assert profile.strategy_for(Stage.RECALL) == "narrative_fast"
        assert profile.strategy_for(Stage.COMPOSE) == "default"  # unlisted stages keep the default
        # The strategy the profile names exists in the host (a builtin here).
        assert "narrative_fast" in host.all_names("turn.pipeline.recall")
