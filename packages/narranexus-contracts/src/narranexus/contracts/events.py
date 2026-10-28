"""
@file_name: events.py
@author: Bin Liang
@date: 2026-09-03
@description: Host event names and payload shapes (the ``hooks`` kind's vocabulary).

Naming follows the VS Code API guideline ``onDid|onWill + Verb + Subject``.
Batch 0 declares the vocabulary and the bus; the platform starts emitting at
these observation points in later batches (D9). A name not in ``HOST_EVENTS``
must be ``declare``d on the bus before it can be subscribed to, so typos fail
loud instead of silently never firing.

Contract version: ``API_VERSIONS["events"]``.
"""
from __future__ import annotations

from typing import Any, TypedDict

HOST_EVENTS: tuple[str, ...] = (
    "onWillStartRun",
    "onDidStartRun",
    "onDidCompleteRun",
    "onDidCancelRun",
    "onDidPersistTurn",
    "onDidChangeArtifact",
    "onDidReceiveChannelMessage",
    "onDidReplyChannelMessage",
    "onDidActivatePlugin",
    "onDidResolveBootstrapGreeting",
    "onDidChangeUserRunnability",
    "onDidChangeAgentName",
    "onDidSettleAgentName",
    "onWillExportManagedChannels",
    "onDidStartBackend",
)


class RunEvent(TypedDict):
    """Payload for the ``*Run`` events."""

    run_id: str
    agent_id: str
    user_id: str
    working_source: str


class TurnEvent(TypedDict):
    """Payload for ``onDidPersistTurn``."""

    run_id: str
    agent_id: str
    user_id: str
    event_id: str
    narrative_ids: list[str]


class ArtifactEvent(TypedDict):
    """Payload for ``onDidChangeArtifact``."""

    artifact_id: str
    agent_id: str
    user_id: str
    change: str


class ChannelMessageEvent(TypedDict):
    """Payload for the channel message events."""

    channel: str
    agent_id: str
    conversation_id: str
    message_id: str


class PluginEvent(TypedDict):
    """Payload for ``onDidActivatePlugin``."""

    plugin_id: str
    version: str
    host: str


class BootstrapGreetingEvent(TypedDict):
    """Payload for ``onDidResolveBootstrapGreeting``: the platform resolved a
    first-turn greeting for a bootstrapping agent and names the head chat
    instance it belongs to; whoever owns chat history seeds it (idempotently).
    ``turn_started_at`` is the turn's event timestamp (datetime)."""

    agent_id: str
    user_id: str
    instance_id: str
    greeting: str
    turn_started_at: Any


class UserRunnabilityEvent(TypedDict):
    """Payload for ``onDidChangeUserRunnability``: something that gates whether
    a user's agents can run changed (login, quota top-up, provider/slot save).
    builtin.job re-arms that user's PAUSED_NO_QUOTA jobs on it."""

    user_id: str


class AgentNameChangeEvent(TypedDict):
    """Payload for ``onDidChangeAgentName``: the rename transaction changed the
    agent row's name; whoever keeps an identity record (builtin.awareness)
    records the change. ``db`` is the caller's client (same connection)."""

    db: Any
    agent_id: str
    old_name: str
    new_name: str


class AgentNameSettledEvent(TypedDict):
    """Payload for ``onDidSettleAgentName``: the agent row now holds ``name``
    (after a rename, or a bundle import that landed under a different name);
    whoever keeps an identity record reconciles a profile that still asserts
    another name. Implementations return the reconcile result (False = miss)."""

    db: Any
    agent_id: str
    name: str


class ManagedChannelCredentialsEvent(TypedDict):
    """Payload for ``onWillExportManagedChannels``: the host is about to export
    the managed-channel inventory to Manyfold; every IM channel
    builtin returns its enabled bindings with decoded credentials as uniform
    rows (provider / agent_id / enabled / external_id / credentials / config)
    for the Manyfold inventory; the route concatenates the results."""

    db: Any


class BackendStartedEvent(TypedDict):
    """Payload for ``onDidStartBackend``: the backend host finished its startup
    critical path and runs post-start work (catalog seeds, warm-ups) in a
    background task. Fired once per process, off the request path, with the
    host's database client; a plugin that seeds a registry-host catalog
    (builtin.teams' marketplace templates) does so here."""

    db: Any


EventPayload = dict[str, Any]

# Which payload shape each host event carries; the kernel declares one hook
# spec per event from this table (params = the TypedDict's keys).
HOST_EVENT_PAYLOADS: dict[str, type] = {
    "onWillStartRun": RunEvent,
    "onDidStartRun": RunEvent,
    "onDidCompleteRun": RunEvent,
    "onDidCancelRun": RunEvent,
    "onDidPersistTurn": TurnEvent,
    "onDidChangeArtifact": ArtifactEvent,
    "onDidReceiveChannelMessage": ChannelMessageEvent,
    "onDidReplyChannelMessage": ChannelMessageEvent,
    "onDidActivatePlugin": PluginEvent,
    "onDidResolveBootstrapGreeting": BootstrapGreetingEvent,
    "onDidChangeUserRunnability": UserRunnabilityEvent,
    "onDidChangeAgentName": AgentNameChangeEvent,
    "onDidSettleAgentName": AgentNameSettledEvent,
    "onWillExportManagedChannels": ManagedChannelCredentialsEvent,
    "onDidStartBackend": BackendStartedEvent,
}


def host_event_params(name: str) -> tuple[str, ...]:
    """The parameter names a hook implementation of ``name`` may accept."""
    return tuple(HOST_EVENT_PAYLOADS[name].__annotations__)

__all__ = [
    "HOST_EVENTS",
    "HOST_EVENT_PAYLOADS",
    "host_event_params",
    "RunEvent",
    "TurnEvent",
    "ArtifactEvent",
    "ChannelMessageEvent",
    "BootstrapGreetingEvent",
    "UserRunnabilityEvent",
    "AgentNameChangeEvent",
    "AgentNameSettledEvent",
    "ManagedChannelCredentialsEvent",
    "BackendStartedEvent",
    "PluginEvent",
    "EventPayload",
]
