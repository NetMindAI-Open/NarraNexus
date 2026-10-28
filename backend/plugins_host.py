"""
@file_name: plugins_host.py
@author: Bin Liang
@date: 2026-09-03
@description: The backend host's consumption of plugin contributions: mounting ``backend.routes`` entries.

Called once from ``backend.main`` after the shell's own routers and before
the SPA fallback, so a plugin route can never shadow a shell route and the
catch-all can never swallow a plugin route. Non-builtin owners are forced
under ``/api/x/<plugin id>``; a spec that claims another prefix is refused
(the plugin is reported, not mounted). ``auth="none"`` routers are recorded
as auth-exempt prefixes — the only way a plugin obtains a public endpoint,
and always an explicit one.

The MANIFEST is the authority on both route-level policy flags
(``auth="none"`` and ``quota_bypass``), and ONE helper — ``RoutePolicy`` —
enforces that on BOTH mount paths. There are two of them (the eager
``mount_plugin_routes`` for everything registered at import, the lazy
``LazyRouterApp`` for user plugins) and each policy flag used to be wired in
only one of them: an eager ``auth="none"`` bypassed the manifest check
entirely, and ``quota_bypass`` for a user plugin was registered INSIDE
activation, i.e. after the middleware that consults it had already 402'd the
request and kept the plugin from ever activating. A new flag on
``RouterSpec`` is now added to ``RoutePolicy`` once, not to two mount sites.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from narranexus.contracts.route import RouterSpec, plugin_route_prefix
from narranexus.kernel.plugins.registries import Registries
from narranexus.contracts.distribution import is_builtin_id

from backend import auth as _auth
from backend.plugin_sdk_host import install_web_host

# Mounting a plugin router and being able to answer "who is calling / may they
# touch this agent / what is the upload ceiling" are the same event: a plugin
# route body calls the ``contracts.web.WebHost`` seam, so the host publishes it
# HERE, at import of the module that does the mounting. Anywhere later (the
# lifespan boot) would leave a window in which a mounted route cannot resolve
# it — and any process that mounts plugin routes without going through
# ``backend.main`` (tests build their own app and Registries) would have no
# seam at all.
install_web_host()

ROUTES_SLOT = "backend.routes"


class LazyRouterApp:
    """An ASGI app mounted at a plugin's prefix that builds the real router on the first request.

    The plugin's route handlers are imported (its activation) only when
    someone actually calls a route under its prefix — so a hundred installed
    plugins cost nothing at boot. The first request awaits ``activate``
    (a coroutine returning the ``RouterSpec``); a failure is remembered and
    answered as 503 with the plugin id, never retried in a tight loop.
    """

    def __init__(self, prefix: str, activate: Callable[[], Awaitable[RouterSpec]], *, plugin_id: str) -> None:
        self.prefix = prefix
        self.plugin_id = plugin_id
        self._activate = activate
        self._app: Any = None
        self._error: str | None = None
        self._lock = asyncio.Lock()

    async def _ensure(self) -> Any:
        if self._app is not None or self._error is not None:
            return self._app
        async with self._lock:
            if self._app is None and self._error is None:
                try:
                    from fastapi import FastAPI

                    spec = await self._activate()
                    sub = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
                    sub.include_router(spec.router)
                    self._app = sub
                except Exception as exc:  # noqa: BLE001 - isolate the plugin
                    self._error = f"{type(exc).__name__}: {exc}"
                    logger.warning(f"[plugins] {self.plugin_id}: lazy router failed: {self._error}")
        return self._app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            # A WebSocket handshake cannot be answered with an HTTP response;
            # closing the socket is the honest refusal.
            if scope.get("type") == "websocket":
                await send({"type": "websocket.close", "code": 1003})
            return
        app = await self._ensure()
        if app is None:
            from starlette.responses import JSONResponse

            response = JSONResponse({"detail": f"plugin {self.plugin_id} failed to activate", "error": self._error}, status_code=503)
            await response(scope, receive, send)
            return
        await app(scope, receive, send)

    def reset(self) -> None:
        """Forget a failed activation so the next request tries again (after the plugin was fixed / re-enabled)."""
        self._app = None
        self._error = None

    @property
    def activated(self) -> bool:
        return self._app is not None

    @property
    def error(self) -> str | None:
        return self._error


def mount_lazy_router(app: Any, plugin_id: str, prefix: str, activate: Callable[[], Awaitable[RouterSpec]]) -> LazyRouterApp:
    """Mount a lazy router for a user plugin under its own prefix (the auth middleware still runs first)."""
    if not prefix.startswith(plugin_route_prefix(plugin_id)):
        raise ValueError(f"{plugin_id}: lazy router prefix {prefix!r} is outside {plugin_route_prefix(plugin_id)!r}")
    lazy = LazyRouterApp(prefix, activate, plugin_id=plugin_id)
    app.mount(prefix, lazy, name=f"plugin:{plugin_id}")
    return lazy


@dataclass
class MountReport:
    mounted: list[tuple[str, str, str]] = field(default_factory=list)  # (owner, name, prefix)
    refused: list[tuple[str, str, str]] = field(default_factory=list)  # (owner, name, reason)


def _normalise_declared(pid: str, prefix: str, entries: Any, field_name: str) -> frozenset[str]:
    """Manifest-declared prefixes as absolute paths under the plugin's own prefix.

    An entry may be written relative (``webhook``) or absolute
    (``/api/x/acme.w/webhook``); anything that does not land under the plugin's
    own prefix is dropped with a warning rather than silently trusted.
    """
    base = prefix.rstrip("/")
    out: set[str] = set()
    for raw in entries or ():
        # Absolute entries are taken as written (and then checked); only a
        # RELATIVE entry is joined onto the plugin's prefix. Joining an
        # absolute one instead would turn `/api/agents` into
        # `/api/x/<id>/api/agents` — inside the plugin's own prefix, so the
        # out-of-prefix check would pass and the warning never fire.
        text = str(raw)
        full = text if text.startswith("/") else base + "/" + text.strip("/")
        full = full.rstrip("/") or full
        if not _auth.path_under_prefix(full, base):
            logger.warning(f"[plugins] {pid}: {field_name} entry {raw!r} is outside {base!r}; ignored")
            continue
        out.add(full)
    return frozenset(out)


@dataclass(frozen=True)
class RoutePolicy:
    """What the plugin's MANIFEST permits its routers to do, for both mount paths.

    ``trusted`` is the builtin escape hatch: builtins are host code (their
    prefixes are already reserved by the manifest parser), so their specs are
    taken at face value. Everyone else must have declared the prefix in the
    manifest the user approved — a value the plugin computes at runtime is
    never enough.
    """

    owner: str
    public: frozenset[str] = frozenset()
    quota_bypass: frozenset[str] = frozenset()
    trusted: bool = False

    def refusal(self, spec: RouterSpec, name: str) -> str | None:
        """Why this spec may not mount, or None."""
        if self.trusted:
            return None
        target = spec.prefix.rstrip("/") or spec.prefix
        if spec.auth == "none" and target not in self.public:
            return (f"route {name!r} is auth='none' but {spec.prefix!r} is not in "
                    f"the manifest's backend.publicPrefixes")
        if spec.quota_bypass and target not in self.quota_bypass:
            return (f"route {name!r} sets quota_bypass but {spec.prefix!r} is not in "
                    f"the manifest's backend.quotaBypassPrefixes")
        return None

    def register(self) -> None:
        """Publish the DECLARED prefixes to the auth middleware, at mount time.

        Before any request: the middleware runs ahead of the (possibly lazy)
        router, so an exemption registered during activation is an exemption
        that never happens — the 401/402 keeps the activation from running.
        Module attributes, not import-time bindings: tests swap the sets.
        """
        _auth.PLUGIN_EXEMPT_PREFIXES.update(self.public)
        _auth.PLUGIN_QUOTA_BYPASS_PREFIXES.update(self.quota_bypass)


def route_policy(owner: str, manifest: Any, prefix: str) -> RoutePolicy:
    if is_builtin_id(owner):
        # Host code: its prefixes are not confined to /api/x/<id> at all, so
        # normalising against that prefix would be meaningless. Nothing to
        # declare, nothing to check.
        return RoutePolicy(owner=owner, trusted=True)
    backend = getattr(manifest, "backend", None) if manifest is not None else None
    return RoutePolicy(
        owner=owner,
        public=_normalise_declared(owner, prefix, getattr(backend, "publicPrefixes", ()), "publicPrefixes"),
        quota_bypass=_normalise_declared(owner, prefix, getattr(backend, "quotaBypassPrefixes", ()), "quotaBypassPrefixes"),
    )


def _check_prefix(owner: str, spec: RouterSpec) -> str | None:
    if is_builtin_id(owner):
        return None
    allowed = plugin_route_prefix(owner)
    if spec.prefix == allowed or spec.prefix.startswith(allowed + "/"):
        return None
    return f"prefix {spec.prefix!r} is outside {allowed!r}"


def mount_plugin_routes(app: Any, registries: Registries, manifests: Any = None) -> MountReport:
    """Mount every ``backend.routes`` contribution onto ``app``.

    ``manifests`` is the loaded manifest per owner; it defaults to what
    ``register_builtins_for_import`` loaded a moment earlier, so the policy
    check costs no second filesystem ``discover()``. An owner with no manifest
    in hand gets an EMPTY policy — fail closed: its routers mount, but a
    runtime ``auth="none"`` / ``quota_bypass`` is refused.
    """
    report = MountReport()
    by_owner = _manifests_by_owner(manifests)
    registry = registries.registry_for(ROUTES_SLOT)
    for entry in registry.entries():
        try:
            spec = entry.factory()
        except Exception as exc:  # noqa: BLE001 — a broken factory must not take the host down
            report.refused.append((entry.owner, entry.name, f"{type(exc).__name__}: {exc}"))
            logger.warning(f"[plugins] {entry.owner}: route {entry.name!r} factory failed: {exc}")
            continue
        if not isinstance(spec, RouterSpec):
            report.refused.append((entry.owner, entry.name, f"not a RouterSpec: {type(spec).__name__}"))
            continue
        problem = _check_prefix(entry.owner, spec)
        if problem:
            report.refused.append((entry.owner, entry.name, problem))
            logger.warning(f"[plugins] {entry.owner}: route {entry.name!r} refused: {problem}")
            continue
        policy = route_policy(entry.owner, by_owner.get(entry.owner), plugin_route_prefix(entry.owner))
        problem = policy.refusal(spec, entry.name)
        if problem:
            report.refused.append((entry.owner, entry.name, problem))
            logger.warning(f"[plugins] {entry.owner}: {problem}")
            continue
        # One entry per spec; backend.auth matches it on a path-segment boundary.
        if spec.auth == "none":
            _auth.PLUGIN_EXEMPT_PREFIXES.add(spec.prefix.rstrip("/") or spec.prefix)
        if spec.quota_bypass:
            _auth.PLUGIN_QUOTA_BYPASS_PREFIXES.add(spec.prefix.rstrip("/") or spec.prefix)
        app.include_router(spec.router, prefix=spec.prefix, tags=list(spec.tags) or [entry.owner])
        report.mounted.append((entry.owner, entry.name, spec.prefix))
    if report.mounted:
        logger.info(f"[plugins] mounted {len(report.mounted)} plugin router(s): {[m[2] for m in report.mounted]}")
    return report


def mount_user_plugin_routes(app: Any, registries: Registries, manifests: Any = None) -> dict[str, LazyRouterApp]:
    """Mount a ``LazyRouterApp`` at ``/api/x/<id>`` for every user plugin that declares
    ``backend.routes`` (discovered from registry.json at import; the code is not touched).
    The first request under the prefix resolves the plugin's route contributions from the
    registries (registered by the lifespan boot) and includes every router it owns."""
    from narranexus.kernel.deployment import is_cloud_mode
    from narranexus.kernel.plugins.compat import host_version
    from narranexus.kernel.plugins.loader import discover

    if manifests is None:
        if is_cloud_mode():
            return {}
        manifests = [m for m in discover(cloud=False, host_version=host_version()).manifests if not m.is_builtin]
    mounted: dict[str, LazyRouterApp] = {}
    for manifest in manifests:
        if manifest.is_builtin or ROUTES_SLOT not in manifest.provides:
            continue
        prefix = plugin_route_prefix(manifest.id)
        # The manifest's declared prefixes go live BEFORE the first request,
        # for the reason spelled out on RoutePolicy.register().
        policy = route_policy(manifest.id, manifest, prefix)
        policy.register()

        def _activate(pid: str = manifest.id, prefix: str = prefix, policy: RoutePolicy = policy) -> Awaitable[RouterSpec]:
            async def run() -> RouterSpec:
                from fastapi import APIRouter

                # (name, spec) pairs: the refusal message must name the SPEC
                # being rejected, not whatever the loop variable last held.
                specs: list[tuple[str, RouterSpec]] = []
                for entry in registries.registry_for(ROUTES_SLOT).entries():
                    if entry.owner != pid:
                        continue
                    spec = entry.factory()
                    if not isinstance(spec, RouterSpec):
                        raise TypeError(f"{pid}: route {entry.name!r} is not a RouterSpec")
                    problem = _check_prefix(pid, spec)
                    if problem:
                        raise ValueError(f"{pid}: route {entry.name!r} refused: {problem}")
                    specs.append((entry.name, spec))
                if not specs:
                    raise LookupError(f"{pid}: no backend.routes contribution loaded (plugin not booted or isolated)")
                combined = APIRouter()
                for name, spec in specs:
                    sub = spec.prefix[len(prefix):]  # /api/x/<id>/extra → /extra under the lazy mount
                    problem = policy.refusal(spec, name)
                    if problem:
                        raise ValueError(f"{pid}: {problem}")
                    combined.include_router(spec.router, prefix=sub, tags=list(spec.tags) or [pid])
                return RouterSpec(combined, prefix, tags=specs[0][1].tags)

            return run()

        mounted[manifest.id] = mount_lazy_router(app, manifest.id, prefix, _activate)
    if mounted:
        logger.info(f"[plugins] lazy routers mounted for user plugin(s): {sorted(mounted)}")
    return mounted


# ------------------------------------------------------------------ boot-at-import


_IMPORT_MANIFESTS: dict[str, Any] = {}


def _manifests_by_owner(manifests: Any) -> dict[str, Any]:
    if manifests is None:
        return _IMPORT_MANIFESTS
    if isinstance(manifests, dict):
        return manifests
    return {m.id: m for m in manifests}


def register_builtins_for_import(registries: Registries) -> tuple[str, ...]:
    """Load every builtin manifest for the backend role at import time and drop disabled ones.

    ``backend.main`` mounts plugin routers at import (so the route table is
    fixed before serving and the approval snapshot can see it); the builtin
    contributions must therefore be registered by then. The lifespan boot
    repeats this idempotently and adds user plugins. Returns the disabled
    builtin ids (from registry.json ``builtin_overrides``).
    """
    from narranexus.kernel.deployment import is_cloud_mode
    from narranexus.kernel.plugins.compat import host_version
    from narranexus.kernel.plugins.install.builtin_deps import ensure_builtin_deps
    from narranexus.kernel.plugins.loader import discover, load

    cloud = is_cloud_mode()
    found = discover(cloud=cloud, host_version=host_version())
    for pid in found.disabled_builtins:
        registries.remove_owner(pid)
    stage1 = [m for m in found.manifests if m.is_builtin]
    # Under a distribution the same plugin set the lifespan boot uses: the
    # builtins it leaves out drop their registrations now (their routers must
    # never mount) and its bundled plugins get their package so their
    # contributions register at import like a builtin's.
    from backend.plugins_boot import distribution, registry_store
    from narranexus.hosts.boot import prepare_bundled_plugins

    res = distribution()
    if res is not None:
        res.raise_for_problems()
        selected = {m.id for m in res.manifests}
        disabled = set(found.disabled_builtins)
        for manifest in stage1:
            if manifest.id not in selected:
                registries.remove_owner(manifest.id)
        stage1 = [m for m in res.manifests if m.id not in disabled]
        prepare_bundled_plugins(res, registry_store(), skip=disabled)
    loadable = []
    for manifest in stage1:
        # PROBE only at import (install=False): a network package install
        # during `import backend.main` made the first desktop launch look hung
        # with no readiness endpoint serving. The lifespan boot installs; the
        # factory's install-deps endpoint retries.
        status = ensure_builtin_deps(manifest, cloud=cloud, install=False)
        if status.ok:
            loadable.append(manifest)
        else:
            registries.remove_owner(manifest.id)
            logger.warning(f"[plugins] {manifest.id}: deps_missing at import — routes/workers not mounted: {status.error}")
    load(registries, loadable, role="backend")
    # The manifest handle mount_plugin_routes needs, without a second discover().
    _IMPORT_MANIFESTS.clear()
    _IMPORT_MANIFESTS.update({m.id: m for m in loadable})
    return tuple(found.disabled_builtins)


WORKERS_SLOT = "backend.workers"


@dataclass
class BackendWorkerContext:
    db: Any


async def start_backend_workers(app: Any, registries: Registries, db: Any) -> list[str]:
    """Start every ``backend.workers`` contribution with ``host == "backend"`` inside the API process."""
    from narranexus.contracts.worker import WorkerSpec

    started: list[str] = []
    handles: list[tuple[str, Any, Any]] = []
    if WORKERS_SLOT not in registries.paths() and WORKERS_SLOT not in registries.slots:
        return started
    for entry in registries.registry_for(WORKERS_SLOT).entries():
        try:
            spec = entry.factory()
        except Exception as exc:  # noqa: BLE001 — isolate the plugin
            logger.warning(f"[plugins] {entry.owner}: worker {entry.name!r} spec failed: {exc}")
            continue
        if not isinstance(spec, WorkerSpec) or spec.host != "backend":
            continue
        name = f"{entry.owner}:{spec.name}"
        try:
            handle = await spec.factory(BackendWorkerContext(db))
            task = asyncio.ensure_future(handle.run)
            task.add_done_callback(
                lambda t, n=name: logger.warning(f"[plugins] backend worker {n} exited: {t.exception()}")
                if not t.cancelled() and t.exception() is not None
                else None
            )
            handles.append((name, handle, task))
            started.append(name)
            logger.info(f"[plugins] backend worker {name} started")
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] backend worker {name} failed to start: {exc}")
    app.state.plugin_backend_workers = handles
    return started


async def stop_backend_workers(app: Any) -> None:
    handles = getattr(app.state, "plugin_backend_workers", None) or []
    for name, handle, task in handles:
        try:
            result = handle.stop()
            if asyncio.iscoroutine(result):
                await result
            await asyncio.wait_for(task, timeout=10.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            task.cancel()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] backend worker {name} stop raised: {exc}")
    app.state.plugin_backend_workers = []


__all__ = [
    "BackendWorkerContext",
    "LazyRouterApp",
    "MountReport",
    "ROUTES_SLOT",
    "RoutePolicy",
    "WORKERS_SLOT",
    "mount_lazy_router",
    "mount_plugin_routes",
    "register_builtins_for_import",
    "route_policy",
    "start_backend_workers",
    "stop_backend_workers",
]
