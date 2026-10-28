import asyncio
from pathlib import Path
from types import SimpleNamespace

from narranexus.sdk import Stage
from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_recall_strategy_registers_and_runs(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home") as host:
        (name,) = host.names("turn.pipeline.recall")
        strategy = host.registry("turn.pipeline.recall").get(name)  # the registry builds the strategy
        assert strategy.stage is Stage.RECALL

        async def _run():
            inputs = SimpleNamespace(ctx=SimpleNamespace(narrative_list=None))
            async for _ in strategy.run(inputs):
                pass
            return inputs.ctx.narrative_list

        assert asyncio.run(_run()) == []
