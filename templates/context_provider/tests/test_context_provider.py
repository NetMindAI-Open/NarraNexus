import asyncio
from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_context_provider_registers_and_speaks(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        (name,) = host.names("agent.capabilities.context_providers")
        provider = host.registry("agent.capabilities.context_providers").get(name)  # the registry builds it
        assert provider.name == name and provider.context_cost_hint > 0
        assert asyncio.run(provider.contribute_instructions(None)) == asyncio.run(provider.contribute_instructions(None))
        assert "context" in asyncio.run(provider.contribute_turn_context(None))
