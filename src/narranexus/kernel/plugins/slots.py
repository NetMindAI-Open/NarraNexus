"""
@file_name: slots.py
@author: Bin Liang
@date: 2026-09-03
@description: The slot tree — every extension point the platform or a plugin declares, by path.

A *slot* is a named, contract-bearing hole. Its ``arity`` says whether exactly
one provider fills it (``one``, replaceable) or any number do (``many``,
additive). Slots form a tree by dotted path (``turn.pipeline.act.framework``); the
provider of a composite slot owns the definition of its children, which is how
"replace the whole runtime" and "replace one stage inside it" coexist.

The kernel seeds the roots (``kernel.*``, ``turn.pipeline`` and the first-level
domains); everything below is declared by the plugin that provides the parent.
Declaration is fail-loud: a duplicate path or a child whose parent is not yet
declared raises, because a silently missing slot would surface much later as a
plugin that "just doesn't do anything".
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Literal, Mapping

from narranexus.contracts import API_VERSIONS, RegistryConflict, Stability, UnknownEntry

Arity = Literal["one", "many"]

_PATH_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)*$")


def validate_path(path: str) -> str:
    if not _PATH_RE.match(path):
        raise ValueError(
            f"invalid slot path {path!r}: dotted lowercase identifiers only (e.g. 'turn.recall')"
        )
    return path


@dataclass(frozen=True)
class Slot:
    """One extension point."""

    path: str
    arity: Arity
    contract: str  # "module.path:Symbol" of the Protocol / HookSpec that fills it
    owner: str  # plugin id that declared it ("builtin.kernel" for seeded roots)
    default: str | None = None  # provider plugin id (one-arity) or None
    stability: Stability = Stability.ALPHA
    distribution_only: bool = False  # only bindable from the distribution/default layers
    doc: str = ""
    meta: Mapping[str, Any] = field(default_factory=dict)
    # The contract KIND (a key of ``contracts.API_VERSIONS``) the slot's entries
    # are written against: the registry backing the slot carries that kind's
    # version, so a manifest's ``api[kind]`` is checked against the right
    # number. ``None`` for namespaces and slots whose entries carry no versioned
    # contract. Declared WITH the slot (kernel seed or manifest ``declares``)
    # instead of in a path-keyed table beside it.
    kind: str | None = None
    # Entry names are matched case-insensitively (framework names are).
    case_insensitive: bool = False

    def __post_init__(self) -> None:
        validate_path(self.path)
        if self.arity not in ("one", "many"):
            raise ValueError(f"slot {self.path!r}: arity must be 'one' or 'many'")
        if self.arity == "many" and self.default is not None:
            raise ValueError(f"slot {self.path!r}: a many-arity slot has no single default")
        if self.kind is not None and self.kind not in API_VERSIONS:
            raise ValueError(f"slot {self.path!r}: unknown contract kind {self.kind!r}; known: {sorted(API_VERSIONS)}")

    @property
    def api_version(self) -> int:
        """The contract version entries of this slot are checked against (0 when kind-less)."""
        return API_VERSIONS[self.kind] if self.kind else 0

    def normalize(self, name: str) -> str:
        """Canonical form of an entry name for this slot."""
        return name.strip().lower() if self.case_insensitive else name

    @property
    def parent(self) -> str | None:
        head, _, _ = self.path.rpartition(".")
        return head or None


class SlotTree:
    """Path-keyed registry of slots with parent/child navigation."""

    def __init__(self) -> None:
        self._slots: dict[str, Slot] = {}

    # ------------------------------------------------------------ mutation

    def declare(self, slot: Slot, *, create_namespaces: bool = False) -> Slot:
        """Add a slot. Parents must exist unless ``create_namespaces`` fills them in.

        ``create_namespaces`` is how a plugin declares ``acme.weather.sources``
        without first spelling out ``acme`` and ``acme.weather``: the missing
        ancestors become namespace slots owned by the same plugin.
        """
        if slot.path in self._slots:
            raise RegistryConflict(
                f"slot {slot.path!r} already declared by {self._slots[slot.path].owner!r}"
            )
        parent = slot.parent
        if parent is not None and parent not in self._slots:
            if not create_namespaces:
                raise UnknownEntry(
                    f"slot {slot.path!r}: parent {parent!r} is not declared; declare it first"
                )
            self.declare(namespace_slot(parent, owner=slot.owner), create_namespaces=True)
        self._slots[slot.path] = slot
        return slot

    def declare_all(self, slots: Iterable[Slot]) -> int:
        """Declare every slot of ``slots`` that the tree lacks, SHALLOWEST FIRST.

        Plugins declare independently of each other and of load order: a
        framework plugin's ``turn.pipeline.act.framework.<seat>`` must not
        auto-create ``turn.pipeline.act`` as a namespace before ``builtin.turn``
        declares it as the many-arity stage slot it is. Ordering by depth makes
        every real declaration land before any ancestor it would otherwise be
        filled in for. Returns the number declared.
        """
        declared = 0
        for slot in sorted(slots, key=lambda s: (s.path.count("."), s.path)):
            if slot.path not in self._slots:
                self.declare(slot, create_namespaces=True)
                declared += 1
        return declared

    # -------------------------------------------------------------- lookup

    def get(self, path: str) -> Slot:
        try:
            return self._slots[path]
        except KeyError:
            raise UnknownEntry(f"unknown slot {path!r}. Known: {sorted(self._slots) or '[]'}") from None

    def try_get(self, path: str) -> Slot | None:
        return self._slots.get(path)

    def children(self, path: str) -> tuple[Slot, ...]:
        prefix = path + "."
        return tuple(
            s for p, s in sorted(self._slots.items()) if p.startswith(prefix) and "." not in p[len(prefix):]
        )

    def descendants(self, path: str) -> tuple[Slot, ...]:
        prefix = path + "."
        return tuple(s for p, s in sorted(self._slots.items()) if p.startswith(prefix))

    def paths(self) -> tuple[str, ...]:
        return tuple(sorted(self._slots))

    def roots(self) -> tuple[Slot, ...]:
        """Top-level slots (the domains) in DECLARATION order — the kernel seeds
        first, in the order they are seeded, then plugin-declared namespaces."""
        return tuple(s for s in self._slots.values() if s.parent is None)

    def by_kind(self, kind: str) -> tuple[Slot, ...]:
        """Every slot whose entries are written against contract ``kind`` (path order)."""
        return tuple(self._slots[p] for p in sorted(self._slots) if self._slots[p].kind == kind)

    def to_rows(self) -> list[dict[str, Any]]:
        """Stable, JSON-friendly view for docs generation and the factory UI."""
        return [
            {
                "path": s.path,
                "arity": s.arity,
                "contract": s.contract,
                "owner": s.owner,
                "default": s.default,
                "stability": s.stability.value,
                "distribution_only": s.distribution_only,
                "kind": s.kind,
                "doc": s.doc,
            }
            for s in (self._slots[p] for p in sorted(self._slots))
        ]

    def __contains__(self, path: object) -> bool:
        return path in self._slots

    def __len__(self) -> int:
        return len(self._slots)

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(self._slots))


KERNEL_OWNER = "builtin.kernel"
NAMESPACE_CONTRACT = "narranexus.contracts:Namespace"


def namespace_slot(path: str, *, owner: str, doc: str = "") -> Slot:
    """A pure grouping node: one-arity, filled by its owner, never bound directly."""
    return Slot(path, "one", NAMESPACE_CONTRACT, owner, default=owner, doc=doc or f"Namespace owned by {owner}.")


def build_kernel_slot_tree() -> SlotTree:
    """The roots every process starts from (spec §6.2).

    The kernel seeds the DOMAIN roots (in display order — their ``doc`` is the
    domain title the catalog and the docs generator show) and the slots the
    kernel itself is the authority for. Children that belong to a builtin
    plugin are declared by that plugin's manifest and arrive with it at boot:
    ``builtin.turn`` declares the seven stage slots, the profiles and the
    agent-loop framework seat under the pipeline it provides; ``builtin.prompts``
    declares ``prompt.*``; ``builtin.ui`` declares ``ui.*``. Declaring them here
    would put their definition in the wrong owner. The loader declares every
    manifest's slots BEFORE any plugin provides, so load order never decides
    whether a slot exists.
    """
    tree = SlotTree()
    one = "one"
    many = "many"
    seeds: list[Slot] = [
        Slot("kernel", one, "narranexus.kernel:Kernel", KERNEL_OWNER, default=KERNEL_OWNER,
             distribution_only=True, doc="Kernel (auth / db / secrets / events) — distribution-only"),
        Slot("kernel.db", one, "narranexus.contracts.services:DatabaseBackend", KERNEL_OWNER,
             default="builtin.kernel", distribution_only=True, doc="Database backend (sqlite | sqlite_proxy | mysql)."),
        Slot("kernel.secrets", one, "narranexus.contracts.services:SecretStore", KERNEL_OWNER,
             default="builtin.kernel", distribution_only=True, doc="Secret storage (secret_box | keychain | vault)."),
        Slot("kernel.auth", one, "narranexus.contracts.services:AuthProvider", KERNEL_OWNER,
             default="builtin.auth.local", distribution_only=True, kind="auth",
             doc="Authentication provider; distribution-level choice."),
        Slot("kernel.events", one, "narranexus.contracts.services:EventSink", KERNEL_OWNER,
             default="builtin.kernel", distribution_only=True, doc="Host event bus implementation."),
        # The prompt domain: the system prompt is sections joined by an assembler;
        # builtin.prompts (the root's provider) declares prompt.sections / prompt.assembler.
        Slot("prompt", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default="builtin.prompts",
             doc="Prompt (system-prompt sections and assembler)"),
        Slot("turn", one, "narranexus.contracts.agent.pipeline:TurnPipeline", KERNEL_OWNER,
             default="builtin.turn", doc="Turn pipeline (stages, profiles, agent-loop framework)"),
        Slot("turn.pipeline", one, "narranexus.contracts.agent.pipeline:TurnPipeline", KERNEL_OWNER,
             default="builtin.turn", doc="The whole turn runtime; its provider declares the stage slots."),
        Slot("model", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Models (providers, clients, resolver)"),
        Slot("model.providers", many, "narranexus.contracts.provider:ProviderDriver", KERNEL_OWNER,
             kind="provider", doc="LLM provider drivers (credential/endpoint axis)."),
        Slot("model.clients", many, "narranexus.contracts.llm_client:LlmClient", KERNEL_OWNER,
             kind="llm_client", doc="Helper-LLM protocol clients (atomic call axis)."),
        Slot("model.resolver", one, "narranexus.contracts.llm_client:ModelResolver", KERNEL_OWNER,
             default="builtin.providers", doc="Model-name resolution. Declared only: the helper clients call the pure rule contracts.llm_client.resolve_helper_model directly; no runtime consults this slot yet, so binding it today is a no-op."),
        Slot("agent", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Agent capabilities (modules, tools, memory kinds, MCP, data access)"),
        namespace_slot("agent.capabilities", owner=KERNEL_OWNER,
                       doc="Capability namespace; its six children are the contribution slots (modules, context providers, tools, MCP servers, memory kinds, data access)."),
        Slot("agent.capabilities.memory_kinds", many, "narranexus.contracts.memory:MemoryKindContract", KERNEL_OWNER,
             kind="memory", doc="Memory kinds (recall / commit / reflect participants)."),
        Slot("agent.capabilities.data_access", many, "narranexus.contracts.data_access:DataAccessSpec", KERNEL_OWNER,
             kind="data_access", doc="AgentDataStore method bodies (DirectStore dispatches by name; the store keeps parity rejects/clamps)."),
        Slot("agent.capabilities.modules", many, "narranexus.contracts.agent.capability:Capability", KERNEL_OWNER,
             kind="module", doc="L4 capabilities (XYZBaseModule classes — each module IS a Capability: its lifecycle methods are its stage participations); meta carries plugin_id / channel; what the platform knows about a module is its own ModuleConfig; every module server is mounted by path on the single MCP host."),
        Slot("agent.capabilities.context_providers", many, "narranexus.contracts.agent.capability:ContextProvider", KERNEL_OWNER,
             kind="context_provider", doc="Assemble-only capabilities: a stable instruction section and/or a volatile turn-context section."),
        Slot("agent.capabilities.tools", many, "narranexus.contracts.tool:ToolProvider", KERNEL_OWNER,
             kind="tool", doc="Tool providers (Act participants); plugin tools default to tool_search."),
        Slot("agent.capabilities.mcp_servers", many, "narranexus.contracts.mcp_server:McpServerSpec", KERNEL_OWNER,
             kind="mcp_server", doc="Site-level MCP servers merged into every agent's tool surface."),
        Slot("ingress", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Ingress (channels, triggers)"),
        Slot("ingress.channels", many, "narranexus.contracts.channel:ChannelDescriptor", KERNEL_OWNER,
             kind="channel", doc="IM channels: one ChannelDescriptor per channel (trigger + module + credential schema + routes + ui + transport)."),
        Slot("ingress.triggers", many, "narranexus.contracts.trigger:TriggerSpec", KERNEL_OWNER,
             kind="trigger", doc="Ingress triggers: IM channel listeners (host=channels), clock/queue pollers run as workers (host=workers), on-demand HTTP servers (host=api)."),
        # Kind-LESS on purpose. A channel's message source rides on its own
        # ChannelDescriptor (kind "channel"), so this slot exists only for the
        # sources that are NOT channels — the message bus, the job clock. Giving
        # it kind="channel" would make a manifest declare an api["channel"]
        # version for something that is not a channel; inventing a
        # "message_source" kind would add a contract-version vocabulary entry for
        # a record that never versions independently of "channel". Entries are
        # checked structurally by MessageSourceSpec's own validation instead.
        Slot("ingress.message_sources", many, "narranexus.contracts.channel:MessageSourceSpec", KERNEL_OWNER,
             doc="Non-channel message sources: how a source's replies are recognised and its stored rows labelled (channels declare theirs on their ChannelDescriptor)."),
        Slot("backend", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Backend host (routes, workers, hooks, tables, settings, services)"),
        Slot("backend.routes", many, "narranexus.contracts.route:RouterSpec", KERNEL_OWNER,
             kind="route", doc="HTTP routers mounted by the backend host (plugins under /api/x/<id>)."),
        Slot("backend.tables", many, "narranexus.contracts.table:TableSpec", KERNEL_OWNER,
             kind="table", doc="Database tables (pure data; created by auto_migrate even when the plugin is inactive)."),
        Slot("backend.workers", many, "narranexus.contracts.worker:WorkerSpec", KERNEL_OWNER,
             kind="worker", doc="Supervised background workers (workers process or backend lifespan)."),
        Slot("backend.settings", many, "narranexus.contracts.settings:SettingsSchema", KERNEL_OWNER,
             kind="settings", doc="Per-plugin settings schemas (NXP_<ID>_* env > stored row > default)."),
        Slot("backend.hooks", many, "narranexus.kernel.plugins.hooks:HookImplSpec", KERNEL_OWNER,
             kind="hook", doc="Hook implementations for declared host hooks (pluggy semantics)."),
        Slot("backend.services", many, "narranexus.kernel.plugins.services:ServiceRef", KERNEL_OWNER,
             kind="services", doc="Services a plugin exposes on the service locator: a tuple of (ServiceRef, implementation) pairs, released with the owner."),
        Slot("content", one, "narranexus.contracts:Namespace", KERNEL_OWNER, default=KERNEL_OWNER,
             doc="Content (skills, bundles)"),
        Slot("content.bundles", many, "narranexus.contracts.bundle:BundleSpec", KERNEL_OWNER,
             kind="bundle", doc="Team-template .nxbundle files offered by the marketplace."),
        Slot("content.skills", many, "narranexus.contracts.skill:SkillSpec", KERNEL_OWNER,
             kind="skill", doc="Skill directories (SKILL.md) scanned into every agent's skill catalog."),
        # The frontend shell; builtin.ui (the root's provider) declares ui.* — the
        # eighteen frontend registries (themes, pages, panels, artifact kinds, tool renderers, commands, slot points...).
        Slot("ui", one, "narranexus.contracts.ui:Shell", KERNEL_OWNER, default="builtin.ui",
             distribution_only=True, kind="ui", doc="Frontend (shell, themes, pages, panels, commands, slot points)"),
    ]
    for slot in seeds:
        tree.declare(slot)
    return tree


__all__ = [
    "Arity",
    "Slot",
    "SlotTree",
    "KERNEL_OWNER",
    "NAMESPACE_CONTRACT",
    "build_kernel_slot_tree",
    "namespace_slot",
    "validate_path",
]
