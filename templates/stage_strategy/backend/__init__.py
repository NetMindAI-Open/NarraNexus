from narranexus.sdk import Contribution, Stage


class __PLUGIN_PKG___recall:
    """A Recall stage strategy: decides what the agent remembers this turn.

    ``inputs`` is the platform's stage input (``inputs.ctx`` carries the turn
    context; set ``inputs.ctx.narrative_list`` to what recall found). An async
    generator so the stage can stream progress; yield nothing to stay silent.
    A profile selects it by name (see the ``pipeline_profile`` template).
    """

    stage = Stage.RECALL

    async def run(self, inputs):
        inputs.ctx.narrative_list = []
        return
        yield  # pragma: no cover — makes this an async generator


# The contribution name is the strategy name a profile refers to.
RECALL_STRATEGIES = (Contribution("__PLUGIN_PKG___recall", __PLUGIN_PKG___recall),)


def activate(ctx):
    ctx.log.info("recall strategy registered")
