import asyncio

from narranexus.sdk import Contribution, WorkerSpec


class _Handle:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self.run = self._run()

    async def _run(self) -> None:
        # Long-running loop: do the periodic work, exit on stop().
        while not self._stop.is_set():
            await asyncio.sleep(60)

    def stop(self) -> None:
        self._stop.set()


async def _factory(ctx):
    return _Handle()


# Supervised as "__PLUGIN_ID__:__PLUGIN_PKG___sync" by the workers process. Contribution ids are
# global within a slot (two plugins named "sync" cannot coexist): name yours after the plugin.
WORKERS = (Contribution("__PLUGIN_PKG___sync", lambda: WorkerSpec("__PLUGIN_PKG___sync", _factory, host="workers")),)


def activate(ctx):
    ctx.log.info("worker declared")
