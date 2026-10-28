import asyncio
from pathlib import Path

from narranexus.sdk import CAPABILITY_VOCABULARY
from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_framework_registers_and_runs_a_turn(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        (name,) = host.names("turn.pipeline.act.framework")
        factory = host.registry("turn.pipeline.act.framework").get(name)
        driver = factory(working_path=str(tmp_path))

        async def _run():
            return [e async for e in driver.agent_loop([{"role": "user", "content": "hi"}], {})]

        assert asyncio.run(_run()) == [{"type": "assistant_text", "text": "echo: hi"}]


def test_declared_capabilities_are_in_the_vocabulary(tmp_path):
    """A word outside the vocabulary is a contract violation; a word the driver
    does not honour is worse — the host gates behaviour on the declaration."""
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        (name,) = host.names("turn.pipeline.act.framework")  # builtin frameworks boot alongside
        (entry,) = [e for e in host.registry("turn.pipeline.act.framework").entries() if e.name == name]
        meta = entry.meta["framework"]
        assert meta.capabilities <= CAPABILITY_VOCABULARY
        driver = entry.factory()(working_path=str(tmp_path))
        assert driver.capabilities() == set(meta.capabilities)
