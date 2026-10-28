from narranexus.sdk import Contribution, PipelineProfile, Stage

# A profile names the strategy each stage runs (unlisted stages keep the
# default) and can narrow budgets / capabilities. Strategy names come from the
# turn.pipeline.<stage> registries — the builtins ("default", "narrative_fast",
# "ephemeral", ...) or a strategy this plugin contributes (stage_strategy template).
PROFILES = (
    Contribution(
        "__PLUGIN_PKG___fast",
        lambda: PipelineProfile(id="__PLUGIN_PKG___fast", strategies={Stage.RECALL: "narrative_fast"}),
    ),
)


def activate(ctx):
    ctx.log.info("pipeline profile registered")
