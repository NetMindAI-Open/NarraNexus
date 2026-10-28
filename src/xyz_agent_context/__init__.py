"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: ``xyz_agent_context`` — a one-release alias of ``narranexus.platform`` (plugin platform batch 6a, decision D8).

The domain packages moved to ``narranexus.platform`` (``module`` →
``module_system``). Anything still importing the old name — external scripts,
the deploy repo's compose entrypoints, an older plugin — keeps working for one
release: a ``sys.meta_path`` finder resolves every ``xyz_agent_context.<path>``
to the SAME module object as ``narranexus.platform.<path>`` (so monkeypatches,
``isinstance`` and singletons agree), and this package re-exports the platform
root. One ``DeprecationWarning`` per process names the replacement. The
package (and the entrypoint shims next to it) is removed in the release after
(``REMOVED_AT`` refuses to import once the host reaches it); see
docs/PLUGIN_BATCH6_DEPLOY_LOCKSTEP.md for the deploy-side switch.
"""
from __future__ import annotations

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys
import warnings

_OLD = "xyz_agent_context"
_NEW = "narranexus.platform"
_RENAMED = {"module": "module_system"}


def target_name(fullname: str) -> str:
    """``xyz_agent_context.module.base`` → ``narranexus.platform.module_system.base``."""
    if fullname == _OLD:
        return _NEW
    rest = fullname[len(_OLD) + 1:]
    head, sep, tail = rest.partition(".")
    return f"{_NEW}.{_RENAMED.get(head, head)}{sep}{tail}"


class _AliasLoader(importlib.abc.Loader):
    """Import → the target module object itself; ``python -m`` → the target's code.

    ``import`` must yield the SAME object as the platform module (identity is
    the whole promise), so ``create_module`` returns the imported target and
    ``exec_module`` does nothing. ``runpy`` (``python -m``) never imports the
    module: it asks the loader for ``get_code`` and executes that in a fresh
    ``__main__`` — so the code-access API is forwarded to the target's real
    loader. Without it every ``-m xyz_agent_context.…`` entrypoint died with
    ``AttributeError: '_AliasLoader' object has no attribute 'get_code'``.
    """

    def __init__(self, target: str, target_spec: importlib.machinery.ModuleSpec) -> None:
        self._target = target
        self._target_spec = target_spec

    def create_module(self, spec):  # noqa: D401 — the target module IS the module
        return importlib.import_module(self._target)

    def exec_module(self, module) -> None:
        return None

    # --- code-access API (runpy / inspect / linecache) → the target's loader ---
    def _delegate(self):
        loader = self._target_spec.loader
        if loader is None:
            raise ImportError(f"{self._target} has no loader")
        return loader

    def get_code(self, fullname: str):
        return getattr(self._delegate(), "get_code")(self._target)  # the target's SourceFileLoader has it; the abstract Loader type does not

    def get_source(self, fullname: str):
        return getattr(self._delegate(), "get_source")(self._target)

    def is_package(self, fullname: str) -> bool:
        return self._target_spec.submodule_search_locations is not None

    def get_filename(self, fullname: str) -> str:
        origin = self._target_spec.origin
        if origin is None:
            raise ImportError(f"{self._target} has no file")
        return origin


class _AliasFinder(importlib.abc.MetaPathFinder):
    """Resolves ``xyz_agent_context.*`` to the platform module of the same path."""

    def find_spec(self, fullname, path=None, target=None):
        if not fullname.startswith(_OLD + "."):
            return None
        new = target_name(fullname)
        try:
            spec = importlib.util.find_spec(new)
        except ModuleNotFoundError:
            return None
        if spec is None:
            return None
        _warn_once()
        alias = importlib.machinery.ModuleSpec(
            fullname,
            _AliasLoader(new, spec),
            origin=spec.origin,
            is_package=spec.submodule_search_locations is not None,
        )
        alias.has_location = spec.has_location  # ``python -m`` sets ``__file__`` from this
        return alias


_warned = False


def _warn_once() -> None:
    global _warned
    if not _warned:
        _warned = True
        warnings.warn(
            "xyz_agent_context is an alias of narranexus.platform (module → module_system) and is removed next release — import narranexus.platform instead",
            DeprecationWarning,
            stacklevel=3,
        )


# Hard expiry (charter: no compat shims without a removal hook). The alias is
# a one-release courtesy; once the host is the release after, importing it is
# an error rather than a silent, forgotten layer that keeps competing with the
# plugin importer for ``sys.meta_path[0]``.
REMOVED_AT = "1.22.0"


def _refuse_if_expired() -> None:
    from narranexus.kernel.plugins.compat import Version, host_version

    running = host_version()
    if running != "0.0.0" and Version.parse(running) >= Version.parse(REMOVED_AT):
        raise ImportError(
            f"xyz_agent_context was a one-release alias of narranexus.platform and is gone as of "
            f"{REMOVED_AT} (running {running}) — import narranexus.platform (module → module_system)"
        )


_refuse_if_expired()

if not any(isinstance(f, _AliasFinder) for f in sys.meta_path):
    sys.meta_path.insert(0, _AliasFinder())

from narranexus.platform import *  # noqa: E402,F401,F403 — the root re-exports
from narranexus.platform import __all__ as _platform_all  # noqa: E402
from narranexus.platform import __version__  # noqa: E402,F401

__all__ = list(_platform_all)


def __getattr__(name: str):
    import narranexus.platform as _platform

    return getattr(_platform, name)
