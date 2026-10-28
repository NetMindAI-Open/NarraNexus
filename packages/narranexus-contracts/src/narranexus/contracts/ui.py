"""
@file_name: ui.py
@author: Bin Liang
@date: 2026-09-03
@description: The frontend shell as a slot contract (``ui``), mirrored on the Python side.

The real frontend contribution registries live in TypeScript
(``frontend/src/platform/registries``). The Python side names the shell so a
distribution can bind its own (``Shell`` is the data the backend needs to
serve it) and mirrors the ENTRY SHAPES of the eighteen frontend registries so
the ``ui.*`` slots ``builtin.ui`` declares carry a contract a manifest can be
checked against and the docs can render. The TypeScript ``*Def`` interfaces
are the truth; these records hold the declarative subset a manifest's
``frontend.ui`` section can express (ids, labels, order, routes) — behaviour
(components, callbacks) only exists in the bundle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class Shell:
    """A built frontend shell: asset directory and entry document."""

    id: str
    dist_dir: str
    entry: str = "index.html"


@dataclass(frozen=True)
class Theme:
    """A frontend theme contribution: which design tokens it overrides (the frontend validates against ``@theme``)."""

    id: str
    display_name: str
    tokens: Mapping[str, str] = field(default_factory=dict)
    dark: bool = False


@dataclass(frozen=True)
class Page:
    """A routed page (``ui.pages``): the shell mounts ``path`` under ``layout``; ``guard`` is ``protected`` or ``public``."""

    id: str
    path: str
    layout: str = "app"
    guard: str = "protected"


@dataclass(frozen=True)
class SidebarItem:
    """A sidebar navigation item (``ui.sidebar``)."""

    id: str
    label_key: str
    to: str
    order: int = 100


@dataclass(frozen=True)
class Panel:
    """An agent drawer panel (``ui.panels``); ``category`` picks the rail group."""

    id: str
    label_key: str
    category: str = "config"


@dataclass(frozen=True)
class SettingsSection:
    """A settings page section (``ui.settings_sections``)."""

    id: str
    label_key: str
    order: int = 100


@dataclass(frozen=True)
class Command:
    """A command-palette command (``ui.commands``)."""

    id: str
    label: str
    hint: str = ""


@dataclass(frozen=True)
class ChannelConfig:
    """A channel configuration card (``ui.channels``) keyed by channel id."""

    id: str
    label: str
    order: int = 100


@dataclass(frozen=True)
class MessageRenderer:
    """A chat message renderer (``ui.message_renderers``) tried in ``order``."""

    id: str
    order: int = 100


@dataclass(frozen=True)
class TimelineEvent:
    """A run-timeline event renderer (``ui.timeline_events``) keyed by event type."""

    id: str


@dataclass(frozen=True)
class ToolRenderer:
    """A tool output-row renderer (``ui.tool_renderers``) keyed by the bare tool name.

    The id is the tool name without its ``mcp__<server>__`` namespace. The
    renderer may decline an output it cannot read; the shell then draws its
    generic row, so an output is never hidden behind a plugin.
    """

    id: str


@dataclass(frozen=True)
class ConversationKind:
    """A conversation kind (``ui.conversation_kinds``)."""

    id: str
    label_key: str


@dataclass(frozen=True)
class ArtifactKind:
    """An artifact kind (``ui.artifact_kinds``): the declarative subset — the ``kind`` string the
    backend ships, a label for admin lists and the download extension. The renderer, the edit
    surface and the save mode live in the bundle (``host.registries.artifactKinds``); until the
    plugin activates, the shell renders a gate that fires ``onArtifactKind:<id>``."""

    id: str
    label: str = ""
    download_ext: str = ""


@dataclass(frozen=True)
class SlotComponent:
    """A component mounted at a slot point (composer extensions, sidebar sections, agent card badges, top bar items)."""

    id: str
    when: str = ""
    order: int = 100


@dataclass(frozen=True)
class SlotAction:
    """An action offered at a slot point (chat header actions, message actions)."""

    id: str
    label: str
    when: str = ""
    order: int = 100


__all__ = [
    "ArtifactKind",
    "ChannelConfig",
    "Command",
    "ConversationKind",
    "MessageRenderer",
    "Page",
    "Panel",
    "SettingsSection",
    "Shell",
    "SidebarItem",
    "SlotAction",
    "SlotComponent",
    "Theme",
    "TimelineEvent",
    "ToolRenderer",
]
