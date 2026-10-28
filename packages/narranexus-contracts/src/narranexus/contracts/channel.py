"""
@file_name: channel.py
@author: Bin Liang
@date: 2026-09-04
@description: Contract for IM channels (slot ``ingress.channels``) — one descriptor per channel.

A channel used to be six scattered facts: a trigger class in the trigger
map, a module in module_registry, a credential manager + bind service in the
data-access ``CHANNELS`` table, a ``WorkingSource`` enum member, a
message-source handler and a hard-coded frontend row. ``ChannelDescriptor``
is that record as data: the platform reads every one of those tables from
the registry, and a channel plugin is one descriptor plus its classes.

A channel also declares its MESSAGE SOURCE here (``reply_tools`` /
``row_prefix_template`` / ``reply_extractor_ref`` / ``dedicated_trigger``):
the facts that used to be a module-level ``MessageSourceRegistry.register``
call executed as an import side effect, invisible to the registry, to
``registry.json`` and to distributions. ``ChannelDescriptor.message_source``
projects them as a ``MessageSourceSpec``, the same record a NON-channel
source (the message bus, the job clock) contributes into
``ingress.message_sources``.

``credential_schema`` is the generic bind form (batch 4b) — fields the
platform stores split into public and secret halves; ``transport`` says how
events arrive (``socket`` = the trigger keeps a connection, ``poll`` = it
polls, ``webhook`` = the platform's webhook endpoint feeds it, ``none`` =
credentials only, no inbound). Class references are ``"pkg.mod:Name"``
strings resolved lazily so declaring a channel never imports its SDK.

Contract version: ``API_VERSIONS["channel"]``.
"""
from __future__ import annotations

import importlib
import re
from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, Optional

Transport = Literal["socket", "poll", "webhook", "none"]
FieldKind = Literal["string", "secret", "url", "bool", "int", "select", "email"]


@dataclass(frozen=True)
class CredentialField:
    name: str
    kind: FieldKind = "string"
    label: str = ""
    help: str = ""
    required: bool = True
    options: tuple[str, ...] = ()
    # Shown back to the UI after bind (identity fields such as bot_username).
    public: bool = True

    def __post_init__(self) -> None:
        if not self.name.isidentifier():
            raise ValueError(f"credential field name must be an identifier, got {self.name!r}")
        if self.kind == "secret" and self.public:
            object.__setattr__(self, "public", False)


@dataclass(frozen=True)
class CredentialSchema:
    fields: tuple[CredentialField, ...] = ()
    # The platform can probe the connection after bind (descriptor.service_ref: do_test_connection).
    supports_test: bool = True
    # Identity field the platform treats as the channel-wide unique external id (bot id, app id).
    external_id_field: str = ""
    # Stored value names the platform blanks whenever a binding goes from disabled to
    # ENABLED (GenericCredentialStore: set_enabled(True), patch(enabled=True), upsert(enabled=True)
    # over a disabled row — one versioned write). For transport state that is only valid
    # while the binding is live: a stream cursor kept across a disabled period would make
    # the re-enabled subscriber resume it and deliver everything sent meanwhile. Applied at
    # the transition, not at disable time, because rows reach "disabled" by many routes
    # (set-active, disable_credential, bundle import, a channel's own patch(enabled=False),
    # rows disabled before the declaration existed). Never applied to rows that stay
    # enabled, to fresh binds, or per sync.
    reset_on_reenable: tuple[str, ...] = ()

    def secret_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.kind == "secret" or not f.public)

    def public_names(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.kind != "secret" and f.public)


@dataclass(frozen=True)
class ChannelUi:
    label: str
    icon: str = "message-square"  # lucide icon name the shell maps
    order: int = 100


_CHANNEL_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_]*")


_REF_RE_HINT = "'pkg.mod:Symbol'"


def _validate_ref(owner: str, field_name: str, ref: str) -> None:
    if ref and ref.count(":") != 1:
        raise ValueError(f"{owner}: {field_name} must look like {_REF_RE_HINT}, got {ref!r}")


@dataclass(frozen=True)
class MessageSourceSpec:
    """How one ``working_source`` answers the chat-history pipeline's two questions.

    "Did the agent reply to whoever contacted it this turn?" (``reply_tools`` +
    ``reply_extractor_ref``) and "how is a stored row of this source labelled to
    the LLM?" (``row_prefix_template``). It is pure DATA — the behaviour lives in
    the platform's ``MessageSourceHandler``, which is built from one of these.

    Two kinds of provider:

    * a channel — its ``ChannelDescriptor.message_source`` projects one, so the
      channel stays ONE record and a channel excluded from a distribution takes
      its message source with it;
    * a non-channel source (the message bus, the job clock) — its plugin
      contributes one into the ``ingress.message_sources`` slot.

    Sources that need nothing source-specific (owner chat, ``a2a``,
    ``callback``, ``skill_study``) declare NOTHING: the platform's default
    handler is exactly right for them, and that is the only fallback left.
    """

    name: str
    display_label: str = ""
    """Brand name the agent reads in its origin declaration. Empty derives from ``name``."""

    reply_tools: tuple[str, ...] = ()
    """Substrings of a tool name that count as delivering to this source."""

    owner_visible_reply_tools: Optional[tuple[str, ...]] = None
    """The subset whose output surfaces in the OWNER's web chat. ``None`` = all of
    ``reply_tools`` (right for chat and every IM channel, where the conversation IS
    with the owner). The bus overrides it: its peer sends reach other agents."""

    row_prefix_template: str = "[{name}]"
    """``str.format`` template over the row's flattened ``meta_data`` + ``channel_tag``."""

    reply_extractor_ref: str = ""
    """``"pkg.mod:function"`` of a ``(tool_name, arguments) -> str | None`` extractor,
    resolved LAZILY on first use: naming it must not import the channel's SDK."""

    dedicated_trigger: bool = False
    """True when this source runs its own long-lived trigger process. Read by
    ``im_channel_prefixes()`` (bus re-dispatch and unread-injection guards)."""

    def __post_init__(self) -> None:
        if not self.name or not _CHANNEL_NAME_RE.fullmatch(self.name):
            raise ValueError(f"message source name must be lowercase [a-z0-9_], got {self.name!r}")
        _validate_ref(self.name, "reply_extractor_ref", self.reply_extractor_ref)

@dataclass(frozen=True)
class ChannelDescriptor:
    name: str  # channel key ("lark"); also the WorkingSource value for inbound turns
    display_name: str
    transport: Transport = "socket"
    credential_schema: CredentialSchema = field(default_factory=CredentialSchema)
    # "pkg.mod:Class" references, resolved lazily
    trigger_ref: str = ""  # ChannelTriggerBase subclass (empty: credentials only)
    module_ref: str = ""  # ChannelModuleBase subclass
    credential_manager_ref: str = ""  # per-channel manager until 4d
    credential_read_method: str = "get"
    service_ref: str = ""  # module with do_bind / do_test_connection / do_unbind
    bind_takes: Literal["mgr", "db"] = "mgr"
    # What a bind CALL accepts for a manager-backed channel (the service's do_bind
    # keyword arguments — a pasted bind link, an owner e-mail to resolve, …). Empty
    # means the stored credential schema IS the bind input (plugin channels).
    bind_fields: tuple[CredentialField, ...] = ()
    has_bind: bool = True
    has_test: bool = True
    unbind_service: bool = False  # unbind goes through the service (lark) rather than the manager
    ui: Optional[ChannelUi] = None
    # --- message source (see MessageSourceSpec; ``message_source`` projects these) ---
    reply_tools: tuple[str, ...] = ()
    row_prefix_template: str = ""
    reply_extractor_ref: str = ""  # "pkg.mod:function", resolved lazily on first extraction
    dedicated_trigger: bool = False
    meta: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # ASCII only: str.isalnum() is Unicode-aware, so 'café' used to pass a
        # validator whose message promised [a-z0-9_]. The name becomes a table
        # key, a WorkingSource enum member and a URL segment; keep it plain.
        if not self.name or not _CHANNEL_NAME_RE.fullmatch(self.name):
            raise ValueError(f"channel name must be lowercase [a-z0-9_], got {self.name!r}")
        for ref in (self.trigger_ref, self.module_ref, self.credential_manager_ref):
            if ref and ref.count(":") != 1:
                raise ValueError(f"{self.name}: class reference must look like 'pkg.mod:Class', got {ref!r}")
        _validate_ref(self.name, "reply_extractor_ref", self.reply_extractor_ref)

    @property
    def has_inbound(self) -> bool:
        return bool(self.trigger_ref) and self.transport != "none"

    @property
    def message_source(self) -> "MessageSourceSpec":
        """This channel's message source — the record the reply/label pipeline reads.

        ``display_name`` doubles as the brand label (they were two spellings of one
        fact and had to agree by hand), and ``row_prefix_template`` falls back to the
        channel's own name so a channel that declares nothing still labels its rows
        as ITSELF rather than as the platform UI."""
        return MessageSourceSpec(
            name=self.name,
            display_label=self.display_name,
            reply_tools=self.reply_tools,
            row_prefix_template=self.row_prefix_template or f"[{self.display_name}]",
            reply_extractor_ref=self.reply_extractor_ref,
            dedicated_trigger=self.dedicated_trigger,
        )

    def resolve(self, ref: str) -> Any:
        module_path, _, attr = ref.partition(":")
        return getattr(importlib.import_module(module_path), attr)


__all__ = ["ChannelDescriptor", "ChannelUi", "CredentialField", "CredentialSchema", "FieldKind", "MessageSourceSpec", "Transport"]
