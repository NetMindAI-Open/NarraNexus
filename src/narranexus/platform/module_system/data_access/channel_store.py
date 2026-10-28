"""
@file_name: channel_store.py
@author:
@date: 2026-08-10
@description: ChannelCredentialStore — the MCP data-access seam for per-channel
send credentials (blueprint P2, #2 "mcp zero db creds", PR-A foundation).

Sibling of ``data_access/store.py``'s AgentDataStore, but a SEPARATE seam
(design decision, 2026-08-10 Owner call): AgentDataStore moves narrative data
(awareness/chat/social/job/memory); this seam returns a RAW per-channel secret
(Discord ``bot_token``, Lark ``app_secret``, …) a send tool needs to
authenticate to the external platform at runtime. Different concern, security-
sensitive shape, own Protocol — same transport/factory pattern (env-gated
DirectStore/HttpStore) as AgentDataStore for consistency, not by folding one
into the other.

- DirectStore: local — dispatches ``channel`` to the matching per-channel
  credential manager (from its ``ChannelSpec``) and returns the dataclass
  serialised via ``to_raw_dict()`` (NOT ``to_public_dict()`` — the whole
  reason this seam exists is to carry the secret across the HTTP hop).
- HttpStore: cloud — calls the owner-gated backend endpoints
  (``backend/routes/agents/channel_credentials.py`` for reads, the generic
  ``/api/channels/<channel>/<op>`` routes for writes), forwarding the caller identity
  headers the same way AgentDataStore's HttpStore does. Never raises, but the
  degradation shape differs by call kind: a READ (GET) failure — unreachable,
  non-2xx, unbound, non-JSON — degrades to ``None`` so a send tool falls to its
  "no_credential" branch; a WRITE (POST bind/unbind/test) failure degrades to
  ``{success: False, error}`` (a failed unbind is NOT "unbound").

All 7 channels are wired through ONE ``ChannelSpec`` descriptor per channel (the
``CHANNELS`` table) — manager + read-method + unbind display-name + optional
bind service, each a lazily-resolved name. Adding a channel is genuinely one
``ChannelSpec(...)`` line + a ``to_raw_dict()`` on its dataclass; nothing here
can drift because there is a single source of truth, not parallel dicts.

Reads (``get_credential`` / ``get_agent_name`` / ``get_agent_owner``), the
mapped writes (``bind`` / ``unbind`` / ``test_connection``, which map to existing
owner-gated backend routes), and the generic credential-mutation primitives
(``patch_credential`` / ``put_credential`` / ``delete_credential``) are all
Protocol methods with a DirectStore and an HttpStore side.

lark's CLI-OAuth write flow (setup / the three-click permission advance /
enable-receive) and narramessenger's ``narra-cli`` passthrough are NOW migrated:
the CLI subprocess + OAuth polling stay local (pure compute), but every DB
persistence hops through the seam — lark's partial ``permission_state`` /
top-level-column writes go through the ``patch/put/delete`` primitives (backed by
``channel_credentials.py``'s PATCH/PUT/DELETE routes → the manager's
``apply_patch`` / ``save_raw`` / ``delete_credential``), and narra reads its
bearer + workspace owner through ``get_credential`` / ``get_agent_owner``.
``_lark_mcp_tools.py`` / ``lark_cli_client.py`` / ``narra_cli_client.py`` hold
ZERO ``get_mcp_db_client`` / ``LarkCredentialManager(db)``. Combined with the
job/chat/memory writes on the AgentDataStore seam, the whole mcp-reachable module
tree is credential-free — so the mcp container can drop ``DATABASE_URL`` (with
``NARRANEXUS_BACKEND_URL`` set, every leg is HttpStore).
"""
from __future__ import annotations

from collections.abc import Iterator, Mapping

from dataclasses import dataclass
from typing import Any, Optional, Protocol
from urllib.parse import quote

from loguru import logger


def _seg(value: str) -> str:
    """Percent-encode an id used as a URL PATH SEGMENT — see store.py's ``_seg``
    for the full rationale (LLM-supplied ids may contain ``?``/``#``/``..``)."""
    return quote(str(value), safe="")


def deep_merge(base: dict, patch: dict) -> dict:
    """Merge ``patch`` INTO a copy of ``base``: where BOTH sides hold a dict,
    recurse (so a ``patch_credential`` of ``{"permission_state": {k: v}}`` adds
    ``k`` to the existing blob instead of replacing the whole blob); otherwise
    ``patch`` wins. This is the one shared semantics the credential-mutation
    ``PATCH`` primitive promises — a manager's ``apply_patch`` uses it so the
    local and HTTP paths merge identically.

    Note: only DICTS recurse — a list-valued field is REPLACED wholesale, not
    element-merged (correct for the scalar/dict permission_state blobs this
    serves today; a future channel storing list-valued creds that expects
    append semantics would need its own merge, not this one)."""
    out = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class ChannelCredentialStore(Protocol):
    """Per-channel credential access a send/bind tool needs, transport-agnostic.

    Reads (get_credential/get_agent_name/get_agent_owner) return the RAW
    credential dict (including the secret) or None when unbound; the clean
    writes (bind/unbind/test_connection) return the backend route's
    ``{success, ...}`` envelope. lark's CLI-OAuth writes are NOT here (deferred —
    see the module docstring's "Known gap")."""

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]: ...

    async def get_agent_name(self, agent_id: str) -> str: ...

    async def get_agent_owner(self, agent_id: str) -> str: ...

    async def bind(self, channel: str, agent_id: str, fields: dict) -> dict: ...

    async def unbind(self, channel: str, agent_id: str) -> dict: ...

    async def test_connection(self, channel: str, agent_id: str) -> dict: ...

    # Generic credential-mutation primitives (blueprint P2 lark write leg). Any
    # channel whose manager implements ``apply_patch`` / ``save_raw`` /
    # ``delete_credential`` can be written through these without a per-op route —
    # lark's CLI-OAuth flow is the first user. See the module docstring.
    async def patch_credential(self, channel: str, agent_id: str, patch: dict) -> dict: ...

    async def put_credential(self, channel: str, agent_id: str, raw: dict) -> dict: ...

    async def delete_credential(self, channel: str, agent_id: str) -> dict: ...


# One per-channel descriptor — the SINGLE source of truth. Everything (reads,
# the write-leg, the endpoint allowlist, unbind wording) reads one field of it,
# so there are no parallel channel-keyed dicts to drift out of sync. Adding a
# channel is genuinely one ``ChannelSpec(...)`` line + a ``to_raw_dict()`` on
# its dataclass. All resolution is lazy by NAME (module path strings) so this
# module never imports every channel package just to hold the table.


@dataclass(frozen=True)
class _BindSpec:
    """A channel's do_bind service — present only for channels with a bind tool.

    ``do_bind`` is NOT uniform: discord/slack/telegram take the manager,
    narramessenger's gateway-bind takes the raw db (``takes``). ``do_test_connection``
    lives in the SAME service module, so the status live-check resolves from here
    too (``has_test``)."""

    service_module: str
    do_bind: str = "do_bind"
    takes: str = "mgr"  # "mgr" | "db"
    has_test: bool = True  # service module also defines do_test_connection


@dataclass(frozen=True)
class ChannelSpec:
    manager_module: str
    manager_class: str
    read_method: str = "get"  # "get" | "get_credential" (lark)
    # Backend /unbind route's not-found wording ("no <display_name> credential
    # bound…") — set ONLY for channels whose unbind is reachable through the seam
    # (they have a ``<ch>_unbind`` MCP tool). "" means DirectStore.unbind falls
    # back to the raw channel string (fine: those channels never call it).
    display_name: str = ""
    bind: Optional[_BindSpec] = None  # None → no bind tool (wechat QR / HA panel)
    # Module with a ``do_unbind`` for channels whose unbind is MORE than
    # ``mgr.unbind`` (lark also tears down inbox channels, narramessenger tells
    # its gateway). "" → the uniform ``mgr.unbind`` path. The signature follows
    # ``bind.takes``: ``do_unbind(mgr, agent_id, db)`` or ``do_unbind(db, agent_id)``.
    unbind_service: str = ""
    unbind_takes: str = "mgr"


def _spec_from_descriptor(d: Any) -> ChannelSpec:
    """The data-access view of a ChannelDescriptor (manager + read method + bind/unbind services)."""
    manager_module, _, manager_class = d.credential_manager_ref.partition(":")
    bind = _BindSpec(d.service_ref, takes=d.bind_takes, has_test=d.has_test) if d.has_bind and d.service_ref else None
    return ChannelSpec(
        manager_module,
        manager_class,
        read_method=d.credential_read_method,
        display_name=d.display_name,
        bind=bind,
        unbind_service=d.service_ref if d.unbind_service else "",
        unbind_takes=d.bind_takes,
    )


class _ChannelSpecs(Mapping[str, ChannelSpec]):
    """``CHANNELS``: name -> ChannelSpec, read live from the ``ingress.channels`` registry.

    Adding a channel is a ChannelDescriptor in the plugin (its manifest
    provides ``ingress.channels``); a builtin disabled in registry.json is
    absent here and its seam calls answer "unknown channel".
    """

    def __init__(self, registries: Any = None) -> None:
        self._registries = registries
        self._cache: dict[str, ChannelSpec] | None = None
        self._cache_key: tuple = ()

    def _registry(self):
        regs = self._registries
        if regs is None:
            from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

            regs = KERNEL_REGISTRIES
        return regs.registry_for("ingress.channels")

    def _build(self) -> dict[str, ChannelSpec]:
        # Cached on the registry's name tuple (the pattern ModuleRegistry uses):
        # SUPPORTED_CHANNELS is consulted per HTTP request and a Mapping's
        # items() walks __iter__ + __getitem__, so an uncached build resolved
        # every descriptor N+1 times per call. A builtin disabled through
        # registry.json changes the key and invalidates the cache without a
        # manual hook.
        registry = self._registry()
        key = registry.names()
        if self._cache is not None and key == self._cache_key:
            return self._cache
        out: dict[str, ChannelSpec] = {}
        for entry in registry.entries():
            try:
                d = entry.factory()
                # No registration here. Building this view is a READ (SUPPORTED_CHANNELS
                # is a membership test on an HTTP request path); it used to register the
                # descriptor's WorkingSource, so whether ``WorkingSource("lark")``
                # resolved depended on whether anyone had read the channel map yet. The
                # source is registered once, where the channel enters the registry
                # (channel/contributions.py:register_working_source).
                if d.credential_manager_ref:
                    out[d.name] = _spec_from_descriptor(d)
            except Exception as e:  # noqa: BLE001 — one broken descriptor must not hide every channel
                # Fail CLOSED for that channel: absent from SUPPORTED_CHANNELS → 404,
                # never implicitly allowed. Mirrors TriggerMapView._build.
                logger.warning(f"channel descriptor {entry.name!r} unavailable, skipped ({type(e).__name__}: {e})")
        self._cache, self._cache_key = out, key
        return out

    def __getitem__(self, name: str) -> ChannelSpec:
        return self._build()[name]

    def __iter__(self) -> Iterator[str]:
        return iter(self._build())

    def __len__(self) -> int:
        return len(self._build())

    def __contains__(self, name: object) -> bool:
        return name in self._build()


CHANNELS: Mapping[str, ChannelSpec] = _ChannelSpecs()


class _SupportedChannels:
    """``SUPPORTED_CHANNELS``: the live set of channel names (membership, iteration, len)."""

    def __contains__(self, name: object) -> bool:
        return name in CHANNELS

    def __iter__(self) -> Iterator[str]:
        return iter(sorted(CHANNELS))

    def __len__(self) -> int:
        return len(CHANNELS)

    def __repr__(self) -> str:
        return f"SUPPORTED_CHANNELS({sorted(CHANNELS)})"


SUPPORTED_CHANNELS = _SupportedChannels()


def _spec(channel: str) -> ChannelSpec:
    spec = CHANNELS.get(channel)
    if spec is None:
        raise ValueError(f"unknown channel: {channel!r}")
    return spec


def _manager_class(channel: str):
    """Resolve ``channel`` to its credential-manager class, importing lazily."""
    import importlib

    spec = _spec(channel)
    return getattr(importlib.import_module(spec.manager_module), spec.manager_class)


def _read_method_name(channel: str) -> str:
    """The manager's read method for ``channel`` (get / get_credential / …)."""
    return _spec(channel).read_method


class DirectStore:
    """Local: dispatch to the per-channel credential manager, byte-for-byte
    the pre-seam db access every channel's MCP tools did directly."""

    async def _db(self):
        # The one MCP db entry point (module/base.py) — same loop-aware
        # factory every other MCP tool (and AgentDataStore's DirectStore)
        # goes through.
        from narranexus.platform.module_system.base import XYZBaseModule

        return await XYZBaseModule.get_mcp_db_client()

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]:
        mgr = (_manager_class(channel))(await self._db())
        read = getattr(mgr, _read_method_name(channel))
        cred = await read(agent_id)
        return cred.to_raw_dict() if cred is not None else None

    async def get_agent_name(self, agent_id: str) -> str:
        # Same raw lookup lark's `_get_agent_name` uses (agent_name lives on
        # the channel-agnostic `agents` table, not per-credential) — falls
        # back to the id itself when the agent row or its name is missing.
        db = await self._db()
        row = await db.get_one("agents", {"agent_id": agent_id})
        return (row or {}).get("agent_name", "") or agent_id

    async def get_agent_owner(self, agent_id: str) -> str:
        # created_by (the workspace owner's user id) — NarraMessenger's media
        # send + CLI workspace resolution need it. Returns "" when the agent
        # row is missing (callers treat empty as "no owner signal").
        db = await self._db()
        row = await db.get_one("agents", {"agent_id": agent_id})
        return (row or {}).get("created_by", "") or ""

    async def bind(self, channel: str, agent_id: str, fields: dict) -> dict:
        """Bind via the channel's own do_bind service (byte-identical to what the
        MCP tool did locally). do_bind takes either the manager or the raw db —
        the ChannelSpec's ``_BindSpec.takes`` records which. Returns do_bind's
        {success, error?, data?}."""
        bind = _spec(channel).bind
        if bind is None:
            raise ValueError(f"channel {channel!r} has no bind service")
        import importlib

        do_bind = getattr(importlib.import_module(bind.service_module), bind.do_bind)
        db = await self._db()
        if bind.takes == "mgr":
            return await do_bind((_manager_class(channel))(db), agent_id, **fields)
        return await do_bind(db, agent_id, **fields)

    async def unbind(self, channel: str, agent_id: str) -> dict:
        """mgr.unbind + the nested {"success":True,"data":{"unbound":True}} the
        discord/slack/telegram/wechat `/unbind` routes return, so Direct↔Http are
        parity for the channels that actually expose an unbind MCP tool.

        NOTE for a future narramessenger unbind tool: narra's own `/unbind` route
        returns a FLAT `{"success":True,"unbound":ok}` and reports success even
        when nothing was removed — it does NOT match this envelope. Wire such a
        tool to this seam only after reconciling narra's route to this shape (or
        special-casing narra here); today narra exposes only a bind tool, so the
        mismatch is inert.

        Channels whose unbind is MORE than a credential delete (lark also tears
        down inbox channels) declare a ``unbind_service`` with a
        ``do_unbind(mgr, agent_id, db)`` that owns the full teardown + its own
        envelope; the backend /unbind route returns that function's dict
        VERBATIM (it does NOT reshape it), so DirectStore and HttpStore are
        byte-identical for lark unbind too."""
        spec = _spec(channel)
        db = await self._db()
        mgr = (_manager_class(channel))(db)
        if spec.unbind_service:
            import importlib

            do_unbind = getattr(importlib.import_module(spec.unbind_service), "do_unbind")
            if spec.unbind_takes == "db":
                return await do_unbind(db, agent_id)
            return await do_unbind(mgr, agent_id, db)
        removed = await mgr.unbind(agent_id)
        if not removed:
            name = spec.display_name or channel
            return {"success": False, "error": f"no {name} credential bound for this agent"}
        return {"success": True, "data": {"unbound": True}}

    async def test_connection(self, channel: str, agent_id: str) -> dict:
        """Re-validate the stored credential against the platform (do_test_connection,
        which lives in the same _service module as do_bind). Read-only — no
        mutation — but it needs the manager, so it routes here rather than staying
        a raw get_mcp_db_client call in the status tool. Only the channels whose
        _service defines do_test_connection AND expose a status tool
        (discord/slack/telegram) call this — a clear error beats a raw
        AttributeError for a channel (e.g. narramessenger) that has neither."""
        import importlib

        # A channel with no bind service (wechat/lark/home_assistant) or one
        # whose service has no do_test_connection (narramessenger, has_test=False)
        # gets a clear ValueError — never a bare KeyError / AttributeError.
        bind = _spec(channel).bind
        do_test = (
            getattr(importlib.import_module(bind.service_module), "do_test_connection", None)
            if bind is not None and bind.has_test
            else None
        )
        if do_test is None:
            raise ValueError(
                f"channel {channel!r} has no test_connection (only discord/slack/telegram wire one)"
            )
        return await do_test((_manager_class(channel))(await self._db()), agent_id)

    # -- credential-mutation primitives (delegate to the manager's write API) --

    async def _run_mutation(self, channel: str, method: str, agent_id: str, *args) -> Optional[dict]:
        """Shared body for the three write primitives. Returns None on success,
        or the {"success": False, "error": ...} envelope on a RUNTIME failure —
        DirectStore must NOT raise on those, so it matches HttpStore + the seam's
        "never raises, returns an envelope" contract (before this, apply_patch's
        ValueError propagated on the local leg while the cloud leg returned an
        envelope — the two failure semantics diverged, defeating the seam). A
        channel whose manager lacks the primitive raises ValueError — a DEV error
        (only lark's manager implements the write primitives), surfaced clearly
        like test_connection does, never a bare AttributeError → 500."""
        try:
            db = await self._db()
        except Exception:  # noqa: BLE001 — lazy MySQL pool build can raise
            # Writes flow into lark tools that have no try/except and now check
            # the envelope, so a raised _db() would raw-throw to the model —
            # degrade to an envelope. A connection-build exception's text carries
            # the DB host/port/user (aiomysql "Can't connect to … (111)"), which
            # would ride the envelope → tool → model → user, so surface a STABLE
            # code and keep the detail in the log only.
            logger.exception(f"[channel.{method}] {channel} db unavailable")
            return {"success": False, "error": "db_unavailable"}
        mgr = (_manager_class(channel))(db)
        fn = getattr(mgr, method, None)
        if fn is None:
            raise ValueError(
                f"channel {channel!r} does not support credential {method} — only "
                f"lark's manager implements the write primitives"
            )
        try:
            await fn(agent_id, *args)
            return None
        except ValueError as e:
            # apply_patch's own validation (no credential to patch / unknown
            # field) — a safe, actionable message; surface it verbatim.
            logger.warning(f"[channel.{method}] {channel}: {e}")
            return {"success": False, "error": str(e)}
        except Exception:  # noqa: BLE001 — degrade to an envelope, never raise
            # A DB/connection error mid-write may carry host/port/user — log the
            # detail, surface a stable code (same reason as the _db() branch).
            logger.exception(f"[channel.{method}] {channel} write failed")
            return {"success": False, "error": "write_failed"}

    async def patch_credential(self, channel: str, agent_id: str, patch: dict) -> dict:
        """Partial update — the manager deep-merges (nested dicts like
        permission_state merged key-wise) and saves."""
        return await self._run_mutation(channel, "apply_patch", agent_id, patch) or {"success": True}

    async def put_credential(self, channel: str, agent_id: str, raw: dict) -> dict:
        """Full upsert of a raw cred dict — the manager rebuilds its dataclass
        (its own _cred_from_raw) and saves."""
        return await self._run_mutation(channel, "save_raw", agent_id, raw) or {"success": True}

    async def delete_credential(self, channel: str, agent_id: str) -> dict:
        return await self._run_mutation(channel, "delete_credential", agent_id) or {
            "success": True, "data": {"deleted": True}}


class HttpStore:
    """Cloud: call the owner-gated backend endpoints, forwarding the caller
    identity (see ``factory.current_identity_headers`` — the same header set
    AgentDataStore's HttpStore forwards). Never raises, but the degradation
    shape differs by call kind:
    - reads (GET credential/name/owner): any failure (unreachable / non-2xx /
      unbound / non-JSON) -> ``None``, so the send tool's existing
      "no_credential" branch handles it exactly as an unbound local agent would.
    - writes (POST bind/unbind/test): any failure -> ``{success: False, error}``
      (a failed unbind is NOT "unbound"), matching the route's own envelope.
    """

    def __init__(self, backend_url: str, identity_headers: Optional[dict] = None) -> None:
        self._base = backend_url.rstrip("/")
        self._headers = identity_headers or {}

    async def get_credential(self, channel: str, agent_id: str) -> Optional[dict]:
        path = f"/api/agents/{_seg(agent_id)}/channels/{_seg(channel)}/credential"
        body = await self._get_json(path)
        if body is None:
            return None
        if body.get("bound") is False:
            return None
        return body

    async def get_agent_name(self, agent_id: str) -> str:
        path = f"/api/agents/{_seg(agent_id)}/channels/name"
        body = await self._get_json(path)
        if not body:
            return agent_id
        return body.get("agent_name") or agent_id

    async def get_agent_owner(self, agent_id: str) -> str:
        path = f"/api/agents/{_seg(agent_id)}/channels/owner"
        body = await self._get_json(path)
        if not body:
            return ""
        return body.get("owner_user_id") or ""

    async def bind(self, channel: str, agent_id: str, fields: dict) -> dict:
        # POST the generic owner-gated bind route (same do_bind the local path
        # runs, behind the descriptor's bind_fields check). The route accepts the
        # nx-service identity (check_owned reads request.state.user_id, which
        # auth_middleware sets from the bearer). An unknown/missing field is a
        # {success:False} envelope there where DirectStore would raise TypeError
        # inside do_bind — never-raise holds on this leg; well-formed input is
        # byte-parity.
        return await self._post_json(f"/api/channels/{_seg(channel)}/bind", {"agent_id": agent_id, "fields": dict(fields)})

    async def unbind(self, channel: str, agent_id: str) -> dict:
        return await self._post_json(f"/api/channels/{_seg(channel)}/unbind", {"agent_id": agent_id})

    async def test_connection(self, channel: str, agent_id: str) -> dict:
        return await self._post_json(f"/api/channels/{_seg(channel)}/test", {"agent_id": agent_id})

    # -- credential-mutation primitives: PATCH/PUT/DELETE the generic endpoint --

    def _cred_path(self, channel: str, agent_id: str) -> str:
        return f"/api/agents/{_seg(agent_id)}/channels/{_seg(channel)}/credential"

    async def patch_credential(self, channel: str, agent_id: str, patch: dict) -> dict:
        return await self._send_json("PATCH", self._cred_path(channel, agent_id), patch)

    async def put_credential(self, channel: str, agent_id: str, raw: dict) -> dict:
        return await self._send_json("PUT", self._cred_path(channel, agent_id), raw)

    async def delete_credential(self, channel: str, agent_id: str) -> dict:
        return await self._send_json("DELETE", self._cred_path(channel, agent_id), None)

    async def _post_json(self, path: str, body: dict) -> dict:
        return await self._send_json("POST", path, body)

    async def _send_json(self, method: str, path: str, body: Optional[dict]) -> dict:
        """One HTTP-verb + HTTPError boundary for every WRITE. Writes must ALWAYS
        hand back a {success, error?} dict the tool can relay — never raise, never
        None (a failed unbind is not "unbound") — so transport failures / non-2xx /
        non-JSON all degrade to success:False with a readable reason."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                r = await c.request(method, path, json=body)
        except httpx.HTTPError as e:
            logger.warning(f"[channel-store] backend unreachable {method} {path}: {e}")
            return {"success": False, "error": f"channel backend unreachable ({type(e).__name__})"}
        if r.status_code >= 400:
            logger.warning(f"[channel-store] backend rejected {method} {path}: {r.status_code}")
            return {"success": False, "error": f"channel backend rejected the call ({r.status_code})"}
        try:
            return r.json() or {"success": False, "error": "channel backend returned an empty response"}
        except ValueError:
            return {"success": False, "error": "channel backend returned a non-JSON response"}

    async def _get_json(self, path: str) -> Optional[dict[str, Any]]:
        """One transport + HTTPError boundary, mirroring store.py's HttpStore.
        Returns None on ANY failure (unreachable / non-2xx / non-JSON) — the
        in-band degradation this whole seam exists to guarantee (a send tool
        must never see an exception from a credential lookup)."""
        import httpx

        try:
            async with httpx.AsyncClient(
                base_url=self._base, headers=self._headers, timeout=20.0
            ) as c:
                r = await c.get(path)
        except httpx.HTTPError as e:
            logger.warning(f"[channel-store] backend unreachable GET {path}: {e}")
            return None
        if r.status_code >= 400:
            logger.warning(f"[channel-store] backend rejected GET {path}: {r.status_code}")
            return None
        try:
            return r.json() or {}
        except ValueError:
            logger.warning(f"[channel-store] backend returned non-JSON response for {path}")
            return None
