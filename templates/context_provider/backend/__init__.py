from narranexus.sdk import Contribution


class __PLUGIN_PKG___context:
    """An Assemble-only capability: adds text to the agent's prompt each turn.

    ``contribute_instructions`` must be byte-stable across turns (it joins the
    cacheable prefix); ``contribute_turn_context`` may vary (it joins the
    dynamic tail). Return an empty string to say nothing. ``context_cost_hint``
    is the order of magnitude of tokens added, shown in the factory page.
    """

    name = "__PLUGIN_PKG___context"
    context_cost_hint = 40

    async def contribute_instructions(self, ctx_data):
        return "When the user asks about __DISPLAY_NAME__, answer from the context below."

    async def contribute_turn_context(self, ctx_data):
        return "__DISPLAY_NAME__ context: nothing to report this turn."


CONTEXT_PROVIDERS = (Contribution("__PLUGIN_PKG___context", __PLUGIN_PKG___context),)


def activate(ctx):
    ctx.log.info("context provider registered")
