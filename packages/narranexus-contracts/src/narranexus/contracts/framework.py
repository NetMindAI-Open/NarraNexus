"""
@file_name: framework.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for agent-loop frameworks (slot ``turn.pipeline.act.framework``).

A framework driver runs one agent turn as a stream of raw, provider-agnostic
event dicts. This is the canonical home of the ``AgentLoopDriver`` Protocol
that ``narranexus.platform.agent_framework.loop.driver`` used to define; the
legacy module now re-exports it so every existing import keeps resolving to
the same object.

Contract version: ``API_VERSIONS["framework"]``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, AsyncGenerator, Literal, Protocol, runtime_checkable

# Every string a driver may declare from ``capabilities()``. Declaring a word
# outside this set is a contract violation caught by the contract test base;
# declaring a word the driver does not honour is a bug the orchestrator cannot
# see (it gates behaviour on the declaration), so declare only what ships.
CAPABILITY_VOCABULARY: frozenset[str] = frozenset(
    {
        "steering",
        "plan",
        "resume",
        "fork",
        "sleep",
        "subagent_announce",
        "event_log",
        "interrupt_soft",
        "raw_context",
        "arg_streaming",
        # The driver consumes structured provider messages, so a past turn can
        # be replayed natively (its positioned monologue/tool segments folded
        # back) instead of flattened to prose. A CLI-backed driver flattens at
        # its doorstep and structurally cannot — see
        # ``platform.agent_framework.loop.history_projection``.
        "native_replay",
    }
)


@runtime_checkable
class AgentLoopDriver(Protocol):
    """Runs one agent turn as a stream of raw, provider-agnostic events.

    Conforming drivers yield event dicts that the platform's response processor
    knows how to consume. The contract mirrors the original concrete
    implementation (``ClaudeAgentSDK.agent_loop``) — the reference shape every
    framework adapter must match.

    Concurrency: ``agent_loop`` is an async generator; a driver must be safe to
    instantiate per turn and must stop yielding promptly once ``cancellation``
    reports ``requested()``.
    """

    def agent_loop(
        self,
        messages: list[dict[str, Any]],
        mcp_servers: dict[str, dict[str, Any]],  # {name: {"url": str, "headers": {str: str}?}}
        *,
        streaming: bool = True,
        extra_env: dict[str, str] | None = None,
        cancellation: Any | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        ...

    def capabilities(self) -> set[str]:
        """Feature flags this driver supports beyond the base contract.

        Capability negotiation seam: the orchestrator and frontend switch
        optional behaviour on the declared set instead of hardcoding
        per-framework knowledge. An empty set means "base contract only". The
        remote (HTTP) driver answers from the WRAPPED framework's
        ``FrameworkMeta.capabilities`` intersected with what the executor hop
        can actually carry (``steering``, via ``/steer`` + ``steer_consumed``
        frames — see remote_driver.py / executor_service.py), so its answer is
        registry-derived rather than a name table. The consumer is live: the
        orchestrator gates a run's steerability on
        ``"steering" in driver.capabilities()``.

        Every declared string must come from ``CAPABILITY_VOCABULARY``, and
        must agree with the framework's own ``FrameworkMeta.capabilities``
        (the static twin the host reads when no driver exists yet). Declare
        only what actually ships.
        """
        ...


@dataclass(frozen=True)
class InstallComponent:
    """One pip wheel or npm package, version pinned inside ``requirement``.

    ``requirement`` is passed to the package manager verbatim (an exact pip
    pin such as ``"pkg==1.2.3"``, or an npm spec such as ``"@scope/pkg@1.2.3"``);
    installers never re-derive a version. The live pins are typed exactly once,
    in each framework plugin's ``FrameworkInstall``.
    """

    kind: Literal["pip", "npm"]
    requirement: str


@dataclass(frozen=True)
class FrameworkInstall:
    """How an on-demand framework is installed and how "installed" is detected.

    ``probe_package`` is the Python import name whose presence means the
    framework's code is there; ``user_version_source`` picks which component's
    detected version the UI shows when there is more than one; ``size_hint``
    is the download-size hint shown before installing.
    """

    components: tuple[InstallComponent, ...]
    probe_package: str
    user_version_source: Literal["npm_cli", "pip_pkg"]
    size_hint: str


FrameworkProtocol = Literal["anthropic", "openai", "any"]
"""Which provider protocol a framework can drive on the agent slot.

A CLI-backed framework speaks exactly one protocol because its CLI does;
a framework that drives the provider HTTP API itself declares ``"any"``.
"""


@dataclass(frozen=True)
class FrameworkMeta:
    """Static description of a framework — the ONLY place framework-specific
    facts live. Every host-side table that used to be keyed on a framework
    name (protocol requirement, subscription-card ownership, install probe,
    login marker, the agent's self-description, live steering, native history
    replay, the cloud credential-riding gate) is derived from the framework
    registry's ``Contribution.meta["framework"]`` at call time, so a
    third-party framework is a first-class citizen the moment it registers.
    There is no remaining host-side table keyed on a framework NAME; the one
    thing a framework may not attest about itself is the operator's cloud
    exemption (see ``providers/cloud_policy.py``), because a fail-open
    self-declaration is exactly what that gate exists to prevent.

    ``install`` is ``None`` for frameworks that ship inside the host and a
    ``FrameworkInstall`` for the on-demand ones. ``oauth_source`` names the
    subscription provider card (``user_providers.source``) that ONLY this
    framework's CLI can redeem — ``None`` when the framework accepts no
    subscription credential. ``runtime_name`` is what the agent calls its own
    runtime in prompts (defaults to ``display_name``); ``login_marker`` is the
    ``(subdir, filename)`` under the home directory whose presence means the
    CLI is logged in.

    ``capabilities`` is the STATIC twin of ``AgentLoopDriver.capabilities()``:
    the words in it must come from :data:`CAPABILITY_VOCABULARY` and must
    match what the driver actually implements. It exists because some hosts
    must know a framework's capability WITHOUT constructing its driver — the
    remote (executor) shell decides whether to carry live steering over the
    hop, and history projection decides whether a past turn can be replayed
    natively, both before any driver exists in this process. Declaring a word
    the driver does not honour is worse than omitting it: the orchestrator
    gates behaviour on the declaration (a declared-but-undrained ``steering``
    leaves the user's interjection queued forever).

    ``uses_shared_cli_login`` says the framework can authenticate through a
    credential FILE in the host's HOME (``~/.claude/.credentials.json``,
    ``~/.codex/auth.json``) rather than a per-agent provider key. It defaults
    to ``True`` — fail-closed: a framework that does not think about this
    question is treated as able to ride a shared login, which is what the
    cloud gate refuses for non-staff users. Set it ``False`` only when the
    framework drives the provider API with the key of the card bound to the
    agent slot and REFUSES subscription credentials outright.
    """

    name: str
    display_name: str
    install: FrameworkInstall | None = None
    protocol: FrameworkProtocol = "any"
    oauth_source: str | None = None
    runtime_name: str | None = None
    login_marker: tuple[str, str] | None = None
    capabilities: frozenset[str] = frozenset()
    uses_shared_cli_login: bool = True

    def __post_init__(self) -> None:
        unknown = frozenset(self.capabilities) - CAPABILITY_VOCABULARY
        if unknown:
            raise ValueError(
                f"FrameworkMeta({self.name!r}): capabilities {sorted(unknown)} are not in "
                f"CAPABILITY_VOCABULARY {sorted(CAPABILITY_VOCABULARY)}"
            )
        # Normalise so a plugin passing a set/list/tuple still gets a hashable,
        # frozen field on a frozen dataclass.
        object.__setattr__(self, "capabilities", frozenset(self.capabilities))

    @property
    def agent_protocols(self) -> tuple[str, ...]:
        """Provider protocols accepted on the agent slot, in preference order."""
        return ("anthropic", "openai") if self.protocol == "any" else (self.protocol,)

    @property
    def self_description(self) -> str:
        """How the agent introduces its runtime (a prompt string — edit with care)."""
        return self.runtime_name or self.display_name


__all__ = [
    "CAPABILITY_VOCABULARY",
    "AgentLoopDriver",
    "InstallComponent",
    "FrameworkInstall",
    "FrameworkMeta",
    "FrameworkProtocol",
]
