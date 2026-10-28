"""
@file_name: driver.py
@author: Bin Liang
@date: 2026-05-29
@description: Pluggable agent-loop framework abstraction.

The 7-step pipeline's step_3 runs one agent turn. Historically it
hard-instantiated ``ClaudeAgentSDK``, binding the whole platform to a
single agent framework — exactly the "one switch away from breaking"
risk iron rule #9 forbids. This module introduces a thin Protocol +
registry so a new framework (OpenAI Agents SDK as a full loop,
LangGraph, a home-grown loop, …) is added by REGISTERING a driver,
never by editing step_3.

Two orthogonal abstraction axes already exist in this package:
  - provider axis  -> ``provider_driver/`` (which endpoint / key)
  - framework axis -> THIS module          (which agent-loop protocol)
They compose: a framework driver still resolves its model/endpoint
through the provider layer.

Selection precedence (most specific wins):
  1. explicit ``framework`` arg to ``get_agent_loop_driver()``
     (the per-agent extension point — pass an agent-scoped choice here)
  2. env var ``AGENT_LOOP_FRAMEWORK``
  3. the ``turn.pipeline.act.framework`` binding (``bound_default_framework()``:
     distribution / narranexus.toml / NX_BIND__*), else the slot default
     ``DEFAULT_AGENT_LOOP_FRAMEWORK`` ("nexus_power"). A binding that names a
     framework that is not registered is a loud FrameworkNotInstalledError,
     never a silent fallback.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from loguru import logger

# The Protocol itself is the public contract and lives in narranexus.contracts
# (plugin platform, batch 0). It is re-exported here so every existing import
# of ``AgentLoopDriver`` from this module keeps resolving to the same object.
from narranexus.contracts import Disposable, UnknownEntry
from narranexus.contracts.framework import AgentLoopDriver, FrameworkMeta
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Registry


class FrameworkNotInstalledError(RuntimeError):
    """A registered framework's optional plugin is not installed.

    Distinct from the ``ValueError`` raised for an UNKNOWN framework: the name
    is valid and known, but on the lightweight local build its SDK plugin
    (``claude-agent-sdk`` / ``openai-codex``) has not been installed yet. This
    is a fail-closed stop — the run refuses with an actionable message rather
    than silently falling back to another framework (which would run the user's
    agent on a framework they did not choose).

    Where this surfaces: config time is the PRIMARY guard — the selector greys
    out an uninstalled framework and ``POST /agent-framework`` returns 409, so a
    user cannot normally bind one. This exception is the runtime BACKSTOP for a
    pre-existing binding (a desktop user who upgrades with an agent already set
    to claude_code, or bound-then-uninstalled): it propagates out of the agent
    turn through the normal run-error surface carrying the English message
    below. There is NO dedicated route catch and no per-framework localisation
    yet — a caller that wants a localised, per-framework hint should catch this
    and read ``exc.framework``. Keep this docstring honest about that.
    """

    def __init__(self, framework: str) -> None:
        self.framework = framework
        # Two ways out, both named: install the plugin, or move the binding to
        # a framework that is installed. The second matters for users whose
        # slot was bound to an on-demand framework before onboarding learned
        # to skip uninstalled ones — nothing rewrites that stored binding.
        super().__init__(
            f"Framework '{framework}' is not installed. Install it from "
            f"Settings → Plugins, or switch to an installed framework in "
            f"Settings → Model Defaults (or the agent's Model & framework "
            f"panel) before running."
        )


DriverFactory = Callable[..., AgentLoopDriver]

DEFAULT_AGENT_LOOP_FRAMEWORK = "nexus_power"

FRAMEWORK_SLOT = "turn.pipeline.act.framework"


def framework_registry(registries: Any = None) -> Registry[DriverFactory]:
    """The registry for slot ``turn.pipeline.act.framework`` — resolved at CALL time (a
    module-level constant bound to the process registries made private
    registries in tests invisible to this module). Keys are case-insensitive;
    entries are lazy factories so registering a framework never imports its SDK.
    Populated by the host boot from the framework plugins' manifests."""
    return (registries or KERNEL_REGISTRIES).registry_for(FRAMEWORK_SLOT)


def register_agent_loop_driver(
    name: str, factory: DriverFactory, *, owner: str = "builtin.frameworks"
) -> Disposable:
    """Register a framework driver factory under a case-insensitive name.

    The factory is called with whatever keyword args ``get_agent_loop_driver``
    forwards (currently ``working_path``); it must return an
    ``AgentLoopDriver``. Re-registering a name overrides it (useful for
    tests injecting a fake driver); the returned ``Disposable`` unregisters it
    again, which is how a test cleans up.
    """
    key = name.strip().lower()
    if key in framework_registry():
        logger.debug(f"Overriding agent-loop driver '{key}'")
    return framework_registry().register(key, lambda: factory, owner=owner, replace=True)


def available_agent_loop_frameworks() -> list[str]:
    """Names of all registered frameworks (sorted, for stable logging)."""
    return sorted(framework_registry().names())


def framework_metas() -> list[FrameworkMeta]:
    """Every registered framework's description, in registration order.

    A registration without ``meta["framework"]`` (a test double registered
    through ``register_agent_loop_driver``) is described as a protocol-agnostic
    framework named after its key, so it is selectable everywhere a builtin is.
    """
    metas: list[FrameworkMeta] = []
    for entry in framework_registry().entries():
        meta = entry.meta.get("framework")
        metas.append(meta if isinstance(meta, FrameworkMeta) else FrameworkMeta(entry.name, entry.name))
    return metas


def framework_meta(name: str) -> FrameworkMeta:
    """The registered framework ``name``'s description. Unknown names fail loud
    (``UnknownEntry``) — a framework the registry does not know is never
    silently substituted by the default."""
    key = (name or "").strip().lower()
    for meta in framework_metas():
        if meta.name.lower() == key:
            return meta
    raise UnknownEntry(
        f"{FRAMEWORK_SLOT}: unknown framework {key!r}. Registered: {available_agent_loop_frameworks()}"
    )


def framework_capabilities(name: str) -> frozenset[str]:
    """The registered framework's declared capabilities — the ONE accessor for
    every host that must answer "can this framework be steered / replayed
    natively / …" WITHOUT constructing its driver.

    Fail-closed on an unknown or misbound name (empty set, never a guess): the
    orchestrator gates features ON the answer, so a wrong "yes" leaves a user's
    interjection queued with nothing draining it. The three name-keyed
    frozensets this replaced (``_STEER_CAPABLE_FRAMEWORKS``,
    ``NATIVE_REPLAY_FRAMEWORKS``, and the ``framework != "nexus_power"`` gate in
    step_3) each meant a third-party framework was silently downgraded no
    matter what it implemented.
    """
    try:
        return framework_meta(name).capabilities
    except (UnknownEntry, FrameworkNotInstalledError):
        return frozenset()


def framework_has_capability(name: str, capability: str) -> bool:
    """Whether framework ``name`` declares ``capability`` (see
    ``framework_capabilities`` for the fail-closed rule)."""
    return capability in framework_capabilities(name)


def default_framework_for_protocol(protocol: str) -> str:
    """The framework a freshly onboarded provider card of ``protocol`` lands on.

    Preference: the first INSTALLED framework LOCKED to that protocol (its CLI
    can only drive this kind of card), then the first installed
    protocol-agnostic one, then the bound default. Registration order is the
    builtin manifest order, so the historical pairing (anthropic →
    claude_code, openai → codex_cli) is preserved wherever those plugins are
    installed and degrades to whatever can actually run otherwise.

    Only installed frameworks are candidates: on the lightweight local build
    Claude Code / Codex are registered but ship as on-demand plugins, and a
    fresh user whose first card landed on an uninstalled framework could not
    run a single turn (``FrameworkNotInstalledError``) until they found the
    framework picker. Availability is read from the same probe the driver's
    fail-closed gate uses (``_meta_installed``, behind ``framework_installed``)
    — never from a name list. The registry is walked once.
    """
    wanted = protocol.strip().lower()
    metas = [meta for meta in framework_metas() if _meta_installed(meta)]
    for meta in metas:
        if meta.protocol == wanted:
            return meta.name
    for meta in metas:
        if meta.protocol == "any":
            return meta.name
    return bound_default_framework()


def framework_for_oauth_source(source: str | None) -> str | None:
    """The ONE framework whose CLI can redeem subscription card ``source``
    (``user_providers.source``), or ``None`` for API-key cards and sources no
    framework claims."""
    if not source:
        return None
    for meta in framework_metas():
        if meta.oauth_source == source:
            return meta.name
    return None


def framework_installed(name: str) -> bool:
    """Whether framework ``name`` can run in THIS process.

    A framework that ships inside the host (``install is None``) is available by
    virtue of being registered; an on-demand one is available when its probe
    package is importable (plugin pyenv or base environment — cloud images
    pre-install every SDK, so this reports True there). Unknown names are
    False: never a silent default.
    """
    try:
        meta = framework_meta(name)
    except UnknownEntry:
        return False
    return _meta_installed(meta)


def _meta_installed(meta: FrameworkMeta) -> bool:
    """``framework_installed`` for an already-resolved meta (no second registry
    scan when the caller is iterating ``framework_metas()``)."""
    if meta.install is None:
        return True
    # Imported locally to avoid an import cycle with this package's __init__.
    from narranexus.platform.agent_framework import plugin_paths

    return plugin_paths.package_installed(meta.name, meta.install.probe_package)


def resolve_framework_name(framework: str | None = None) -> str:
    """Apply the selection precedence and return the resolved name."""
    return (
        framework
        or os.getenv("AGENT_LOOP_FRAMEWORK")
        or bound_default_framework()
    ).strip().lower()


def bound_default_framework() -> str:
    """The framework the ``turn.pipeline.act.framework`` binding names (a plugin id such as
    ``builtin.frameworks.claude_code`` → its registered framework name); the code default otherwise."""
    from narranexus.contracts import UnknownEntry
    from narranexus.kernel.plugins.bound import bound_entry, bound_layer, bound_provider

    if bound_layer(KERNEL_REGISTRIES, "turn.pipeline.act.framework") == "DEFAULT":
        return DEFAULT_AGENT_LOOP_FRAMEWORK  # an UNBOUND slot legitimately means the code default
    try:
        return bound_entry(KERNEL_REGISTRIES, FRAMEWORK_SLOT).name
    except UnknownEntry as exc:
        # A misbinding must be loud, never a silent fallback: running the turn
        # on nexus_power while the settings page says "Claude Code" is the
        # substitution FrameworkNotInstalledError exists to refuse.
        provider = bound_provider(KERNEL_REGISTRIES, "turn.pipeline.act.framework")
        raise FrameworkNotInstalledError(
            f"turn.pipeline.act.framework is bound to {provider!r} but no such framework is registered "
            f"({exc}); install/enable that plugin or remove the binding"
        ) from exc


def get_agent_loop_driver(
    framework: str | None = None,
    *,
    executor_url: str | None = None,
    **factory_kwargs: Any,
) -> AgentLoopDriver:
    """Resolve and construct the agent-loop driver for this turn.

    Args:
        framework: explicit framework name; ``None`` falls through to env
            / default. This is the per-agent extension point.
        executor_url: explicit per-user Executor URL (resolved via the
            broker). Overrides the static ``AGENT_EXECUTOR_URL`` env. When
            ``None``/empty, falls back to the env var (local → unset →
            in-process driver).
        **factory_kwargs: forwarded verbatim to the driver factory
            (e.g. ``working_path``).

    Raises:
        FrameworkNotInstalledError: a known plugin framework (claude_code /
            codex_cli) whose optional SDK is not installed on the local build
            (the fail-closed backstop — see the class docstring).
        ValueError: the resolved framework name is not registered — fail
            loud rather than silently fall back, so a typo in config is
            caught immediately instead of masquerading as "claude".
    """
    name = resolve_framework_name(framework)

    # Executor seam (binding rule #7/#9/#20): route the loop to a remote
    # Executor when an executor URL is available — per-user (resolved by
    # the broker, passed as `executor_url`) or the static env fallback
    # (`AGENT_EXECUTOR_URL`). So claude/codex only ever spawn in that one
    # isolated container. No URL (local / desktop, or inside the executor
    # container itself) → in-process driver below, behaviour unchanged.
    resolved_executor_url = (executor_url or os.getenv("AGENT_EXECUTOR_URL", "")).strip()
    if resolved_executor_url:
        from narranexus.platform.agent_framework.loop.remote_driver import (
            RemoteAgentLoopDriver,
        )
        return RemoteAgentLoopDriver(
            framework=name, executor_url=resolved_executor_url, **factory_kwargs
        )

    try:
        factory = framework_registry().get(name)
    except UnknownEntry:
        raise ValueError(
            f"Unknown agent-loop framework '{name}'. "
            f"Registered: {available_agent_loop_frameworks() or '[]'}. "
            f"Register one via register_agent_loop_driver()."
        ) from None

    # Fail-closed on the lightweight local build: a framework whose optional
    # SDK is not installed must refuse here, BEFORE building the driver (whose
    # lazy SDK import would otherwise throw a raw ImportError mid-turn). Which
    # frameworks need a probe is the framework's own ``FrameworkMeta.install``
    # — a host-shipped or custom-registered driver is available by virtue of
    # being registered. Only the in-process path reaches this: the
    # remote-executor branch above returned already, and cloud executors
    # pre-install every SDK so the check passes there.
    if not framework_installed(name):
        raise FrameworkNotInstalledError(name)

    return factory(**factory_kwargs)
