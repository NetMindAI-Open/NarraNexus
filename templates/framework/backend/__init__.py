from narranexus.sdk import CAPABILITY_VOCABULARY, Contribution, FrameworkMeta


class __PLUGIN_PKG___driver:
    """An agent-loop framework: it runs ONE agent turn as a stream of raw event
    dicts. Replacing this slot replaces what "the agent thinks" means.

    ``agent_loop`` is an async generator (a driver must stop yielding promptly
    once ``cancellation.requested()``); ``capabilities`` declares the optional
    features this driver really implements — every word must come from
    ``CAPABILITY_VOCABULARY``, and the host GATES behaviour on the declaration,
    so declaring one you do not honour is worse than declaring none.
    """

    def __init__(self, **factory_kwargs):
        self.working_path = factory_kwargs.get("working_path", ".")

    def capabilities(self) -> set[str]:
        return set(META.capabilities)

    async def agent_loop(
        self,
        messages,
        mcp_servers,
        *,
        streaming: bool = True,
        extra_env=None,
        cancellation=None,
        **kwargs,
    ):
        last = messages[-1]["content"] if messages else ""
        yield {"type": "assistant_text", "text": f"echo: {last}"}


def _factory(**factory_kwargs):
    # Lazy: registering a framework must never import its SDK.
    return __PLUGIN_PKG___driver(**factory_kwargs)


# Every framework fact the host needs BEFORE a driver exists lives here — the
# protocol a provider card must speak, the subscription card only this CLI can
# redeem, the install probe, the capabilities, and whether the framework can
# authenticate from a shared CLI login file (True = fail-closed; leave it True
# unless the framework drives the provider API with the bound card's own key).
META = FrameworkMeta(
    "__PLUGIN_PKG__",
    "__DISPLAY_NAME__",
    protocol="any",
    capabilities=frozenset(),
    uses_shared_cli_login=False,
)
assert META.capabilities <= CAPABILITY_VOCABULARY  # the contract test asserts this too

# §8: a one-arity slot is filled by a symbol named CONTRIBUTION.
CONTRIBUTION = Contribution("__PLUGIN_PKG__", lambda: _factory, meta={"framework": META})


def activate(ctx):
    ctx.log.info("agent-loop framework registered")
