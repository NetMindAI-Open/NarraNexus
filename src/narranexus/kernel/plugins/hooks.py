"""
@file_name: hooks.py
@author: Bin Liang
@date: 2026-09-03
@description: Hook specs and callers — the ``many``-arity extension mechanism (pluggy semantics).

Where a ``Registry`` holds exactly one provider per name, a hook lets any
number of plugins participate in one call. The ordering and composition rules
are pluggy's, because they are the ones that scaled to thousands of pytest
plugins:

- implementations run in LIFO registration order, ``tryfirst`` ones before,
  ``trylast`` ones after;
- a ``wrapper`` is a generator that yields exactly once and observes the
  combined result of the non-wrapper implementations;
- ``firstresult`` specs stop at the first non-``None`` return;
- an implementation declares only the parameters it uses (the caller prunes
  the rest), so adding a parameter to a spec never breaks old plugins;
- any owner can be ``block``ed, including builtins.

Failures are isolated per implementation: one raising hook is recorded in the
``HookOutcome`` under its owner and the others still run. Nothing in here
imports asyncio at module level beyond what is needed to await async
implementations; the bus/turn budget layer decides timeouts.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterator, Mapping

from loguru import logger

from narranexus.contracts import Disposable, RegistryConflict, RegistryFrozen, UnknownEntry


@dataclass(frozen=True)
class HookSpec:
    """Declaration of a hook: its name, the parameters callers pass, and how results combine."""

    name: str
    params: tuple[str, ...]
    firstresult: bool = False
    doc: str = ""


@dataclass(frozen=True)
class HookImpl:
    fn: Callable[..., Any]
    owner: str
    tryfirst: bool = False
    trylast: bool = False
    wrapper: bool = False
    accepted: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class HookImplSpec:
    """What a plugin declares in its manifest-referenced ``HOOKS``: the hook name, the function, the flags.

    Owner and accepted parameters are NOT here — the loader stamps the owner
    from the manifest (a plugin cannot implement a hook on another's behalf)
    and the caller derives the accepted parameters from the spec.
    """

    hook: str
    fn: Callable[..., Any]
    tryfirst: bool = False
    trylast: bool = False
    wrapper: bool = False


def hookimpl(
    hook: str, *, tryfirst: bool = False, trylast: bool = False, wrapper: bool = False
) -> Callable[[Callable[..., Any]], HookImplSpec]:
    """Decorator producing a ``HookImplSpec``: ``@hookimpl("onDidPersistTurn")``."""

    def _wrap(fn: Callable[..., Any]) -> HookImplSpec:
        return HookImplSpec(hook=hook, fn=fn, tryfirst=tryfirst, trylast=trylast, wrapper=wrapper)

    return _wrap


@dataclass
class HookOutcome:
    """What a call produced: ordered results plus per-owner failures."""

    results: list[Any] = field(default_factory=list)
    errors: list[tuple[str, BaseException]] = field(default_factory=list)

    @property
    def first(self) -> Any:
        return self.results[0] if self.results else None


def _accepted_params(fn: Callable[..., Any], spec: HookSpec) -> tuple[str, ...]:
    """The subset of spec params this implementation declares (arg pruning)."""
    sig = inspect.signature(fn)
    accepts_var_kw = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
    if accepts_var_kw:
        return spec.params
    names = tuple(n for n in sig.parameters if n in spec.params)
    unknown = [
        n
        for n, p in sig.parameters.items()
        if n not in spec.params and p.default is inspect.Parameter.empty and p.kind in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    ]
    if unknown:
        raise TypeError(
            f"hook {spec.name!r}: implementation {fn.__qualname__} requires parameters "
            f"{unknown} that the spec does not provide ({list(spec.params)})"
        )
    return names


class HookCaller:
    """All implementations of one ``HookSpec`` and the call algorithm over them."""

    def __init__(self, spec: HookSpec) -> None:
        self.spec = spec
        self._impls: list[HookImpl] = []
        self._frozen = False

    def freeze(self) -> None:
        self._frozen = True

    @property
    def frozen(self) -> bool:
        return self._frozen

    # ------------------------------------------------------------ mutation

    def add(
        self,
        fn: Callable[..., Any],
        *,
        owner: str,
        tryfirst: bool = False,
        trylast: bool = False,
        wrapper: bool = False,
    ) -> Disposable:
        if self._frozen:
            raise RegistryFrozen(f"hook {self.spec.name!r}: cannot add implementations after freeze()")
        if tryfirst and trylast:
            raise ValueError(f"hook {self.spec.name!r}: tryfirst and trylast are exclusive")
        if wrapper and not inspect.isgeneratorfunction(fn) and not inspect.isasyncgenfunction(fn):
            raise TypeError(f"hook {self.spec.name!r}: a wrapper must be a generator function")
        for existing in self._impls:
            if existing.fn is fn and existing.owner == owner:
                # Idempotent like Registry.register: the import-time registration
                # (a package re-imported in tests) and the manifest-driven load
                # name the same function; one implementation, not two calls.
                return Disposable(lambda: None)
        impl = HookImpl(
            fn=fn,
            owner=owner,
            tryfirst=tryfirst,
            trylast=trylast,
            wrapper=wrapper,
            accepted=_accepted_params(fn, self.spec),
        )
        self._impls.append(impl)

        def _dispose() -> None:
            if impl in self._impls:
                self._impls.remove(impl)

        return Disposable(_dispose)

    def block(self, owner: str) -> int:
        """Remove every implementation registered by ``owner``. Returns how many."""
        before = len(self._impls)
        self._impls = [i for i in self._impls if i.owner != owner]
        return before - len(self._impls)

    def owners(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(i.owner for i in self._impls))

    def __len__(self) -> int:
        return len(self._impls)

    # ---------------------------------------------------------------- order

    def _ordered(self, wrappers: bool) -> list[HookImpl]:
        """pluggy order: LIFO registration, tryfirst ahead, trylast behind."""
        pool = [i for i in self._impls if i.wrapper == wrappers]
        pool.reverse()
        first = [i for i in pool if i.tryfirst]
        normal = [i for i in pool if not i.tryfirst and not i.trylast]
        last = [i for i in pool if i.trylast]
        return first + normal + last

    # ----------------------------------------------------------------- call

    async def call(self, **kwargs: Any) -> HookOutcome:
        missing = [p for p in self.spec.params if p not in kwargs]
        if missing:
            raise TypeError(f"hook {self.spec.name!r}: missing call arguments {missing}")
        outcome = HookOutcome()
        wrappers = self._ordered(wrappers=True)
        active: list[tuple[HookImpl, Any]] = []
        # Wrappers: run the "before" half, outermost first.
        for impl in wrappers:
            gen = None
            try:
                gen = impl.fn(**{k: kwargs[k] for k in impl.accepted})
                if inspect.isasyncgen(gen):
                    await gen.__anext__()
                else:
                    next(gen)
                active.append((impl, gen))
            except Exception as exc:  # noqa: BLE001 — isolate; keep going
                outcome.errors.append((impl.owner, exc))
                logger.warning(f"[hook:{self.spec.name}] wrapper {impl.owner!r} failed before: {exc!r}")
                await _close_generator(gen)
        # Plain implementations.
        for impl in self._ordered(wrappers=False):
            try:
                result = impl.fn(**{k: kwargs[k] for k in impl.accepted})
                if inspect.isawaitable(result):
                    result = await result
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append((impl.owner, exc))
                logger.warning(f"[hook:{self.spec.name}] {impl.owner!r} failed: {exc!r}")
                continue
            if result is not None:
                outcome.results.append(result)
                if self.spec.firstresult:
                    break
        # Wrappers: "after" half, innermost first, each sees the outcome.
        for impl, gen in reversed(active):
            try:
                if inspect.isasyncgen(gen):
                    await gen.asend(outcome)
                else:
                    gen.send(outcome)
            except (StopIteration, StopAsyncIteration):
                continue
            except Exception as exc:  # noqa: BLE001
                outcome.errors.append((impl.owner, exc))
                logger.warning(f"[hook:{self.spec.name}] wrapper {impl.owner!r} failed after: {exc!r}")
                continue
            outcome.errors.append(
                (impl.owner, RuntimeError(f"wrapper {impl.fn.__qualname__} yielded more than once"))
            )
        return outcome


async def _close_generator(gen: Any) -> None:
    """Run a failed wrapper's ``finally`` blocks instead of leaving it dangling."""
    if gen is None:
        return
    try:
        if inspect.isasyncgen(gen):
            await gen.aclose()
        elif inspect.isgenerator(gen):
            gen.close()
    except Exception as exc:  # noqa: BLE001 — closing is best effort
        logger.debug(f"[hook] closing a failed wrapper raised: {exc!r}")


class HookRegistry:
    """Name → ``HookCaller``; specs are declared once, implementations any time before freeze."""

    def __init__(self) -> None:
        self._callers: dict[str, HookCaller] = {}
        self._frozen = False

    def declare(self, spec: HookSpec) -> HookCaller:
        existing = self._callers.get(spec.name)
        if existing is not None:
            if existing.spec == spec:
                return existing  # idempotent: the kernel vocabulary may be declared by more than one path
            raise RegistryConflict(f"hook {spec.name!r} already declared with a different signature")
        caller = HookCaller(spec)
        if self._frozen:
            caller.freeze()
        self._callers[spec.name] = caller
        return caller

    def freeze(self) -> None:
        """Registration is a startup activity; after this, ``add`` raises."""
        self._frozen = True
        for caller in self._callers.values():
            caller.freeze()

    @property
    def frozen(self) -> bool:
        return self._frozen

    def caller(self, name: str) -> HookCaller:
        try:
            return self._callers[name]
        except KeyError:
            raise UnknownEntry(f"hook {name!r} is not declared. Known: {list(self._callers) or '[]'}") from None

    def add(self, name: str, fn: Callable[..., Any], *, owner: str, **flags: Any) -> Disposable:
        return self.caller(name).add(fn, owner=owner, **flags)

    def block(self, owner: str) -> int:
        return sum(c.block(owner) for c in self._callers.values())

    def names(self) -> tuple[str, ...]:
        return tuple(self._callers)

    def specs(self) -> Mapping[str, HookSpec]:
        return {n: c.spec for n, c in self._callers.items()}

    def __iter__(self) -> Iterator[str]:
        return iter(self._callers)

    def __contains__(self, name: object) -> bool:
        return name in self._callers


AsyncHookFn = Callable[..., Awaitable[Any]]

__all__ = ["HookSpec", "HookImpl", "HookImplSpec", "HookOutcome", "HookCaller", "HookRegistry", "AsyncHookFn", "hookimpl"]
