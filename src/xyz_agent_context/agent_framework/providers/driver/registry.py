"""
@file_name: registry.py
@author: Bin Liang
@date: 2026-05-13
@description: Driver class registry — module-level map driver_type -> class.

Concrete drivers register themselves with the ``@register`` decorator at
import time. The resolver dispatches on ``card.driver_type`` via
:func:`get_driver_class`. This is the only piece of code that knows the
list of drivers exists, so the resolver doesn't need to import any of
them directly.

SystemDriver is registered conditionally — only when running in cloud
mode (env-backed system free-tier credentials are loaded). Local-mode
processes (DMG / `bash run.sh`) never register it because the
system-default path is dead code there.
"""
from __future__ import annotations

from typing import Optional, Type

from loguru import logger


DRIVER_REGISTRY: dict[str, Type] = {}


def register(driver_cls):
    """Class decorator that registers a Driver under its
    :py:meth:`driver_type` key.

    Idempotent: re-registering the same class is a no-op; registering
    a different class under the same key replaces it (we log a warning
    so test fixtures don't silently leak).
    """
    key = driver_cls.driver_type()
    existing = DRIVER_REGISTRY.get(key)
    if existing is driver_cls:
        return driver_cls
    if existing is not None:
        logger.warning(
            f"[provider_driver] Overwriting registration for driver_type={key!r}: "
            f"{existing.__module__}.{existing.__name__} -> "
            f"{driver_cls.__module__}.{driver_cls.__name__}"
        )
    DRIVER_REGISTRY[key] = driver_cls
    return driver_cls


def get_driver_class(driver_type: str) -> Optional[Type]:
    """Look up a Driver class by its registry key.

    Returns ``None`` for unknown keys — the resolver treats that as a
    fatal config error (raises ``LLMConfigNotConfigured``).
    """
    return DRIVER_REGISTRY.get(driver_type)


__all__ = ["DRIVER_REGISTRY", "register", "get_driver_class"]
