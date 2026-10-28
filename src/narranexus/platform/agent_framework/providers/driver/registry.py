"""
@file_name: registry.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver class registry — driver_type -> class, on the kernel ``Registry[T]``.

Concrete drivers register themselves with the ``@register`` decorator at
import time. The resolver dispatches on ``card.driver_type`` via
:func:`get_driver_class`. This is the only piece of code that knows the
list of drivers exists, so the resolver doesn't need to import any of
them directly.

SystemDriver is registered conditionally — only when running in cloud
mode (env-backed system free-tier credentials are loaded). Local-mode
processes (DMG / `bash run.sh`) never register it because the
system-default path is dead code there.

Since the plugin platform's batch 0 the map is a kernel ``Registry`` for the
``model.providers`` slot: same keys, same idempotent re-registration, plus
owner/metadata bookkeeping and ``freeze()`` for the loader.
"""
from __future__ import annotations

from typing import Any, Optional, Type

from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution, Registry

DRIVERS_SLOT = "model.providers"


def driver_registry(registries: Any = None) -> Registry[Type]:
    """The registry for slot ``model.providers``, resolved at call time; populated by the host boot from the provider plugins' manifests."""
    return (registries or KERNEL_REGISTRIES).registry_for(DRIVERS_SLOT)


def register(driver_cls):
    """Class decorator: attaches the driver's ``Contribution`` (keyed by
    :py:meth:`driver_type`) so a manifest can name it (``<module>:CONTRIBUTION``).

    Registration is NOT an import-time side effect any more: the host boot
    registers what the manifests name. A test that wants a fake driver calls
    ``register_driver(cls, owner=...)`` explicitly.
    """
    key = driver_cls.driver_type()
    driver_cls.contribution = Contribution(key, lambda: driver_cls)
    return driver_cls


def register_driver(driver_cls, *, owner: str, registries: Any = None, replace: bool = False):
    """Explicit registration (tests, an embedding host): the same object the manifest would name."""
    contribution = getattr(driver_cls, "contribution", None) or Contribution(driver_cls.driver_type(), lambda: driver_cls)
    driver_cls.contribution = contribution
    return driver_registry(registries).register_contribution(contribution, owner=owner, replace=replace)


def get_driver_class(driver_type: str) -> Optional[Type]:
    """Look up a Driver class by its registry key.

    Returns ``None`` for unknown keys — the resolver treats that as a
    fatal config error (raises ``LLMConfigNotConfigured``).
    """
    return driver_registry().try_get(driver_type)


__all__ = ["DRIVERS_SLOT", "driver_registry", "get_driver_class", "register", "register_driver"]
