"""
@file_name: host_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: Call a host event (contracts.events) from platform code against the process kernel registries.

Builtins implement host events through ``backend.hooks``, registered by the
manifest loader when the HOST BOOTS — that is the only registration path, and
importing anything (this module included) registers nothing. So the guarantee
this file offers is narrower than it once claimed: it names the EVENT rather
than a module, and it reads whatever the process actually booted.

In a process that never booted the plugin platform the hook registry is empty
and ``call_host_hook`` runs ZERO implementations, returning an empty
``HookOutcome`` — no error, because that is the same answer as "every
implementation of this event is disabled in this distribution", and the callers
of an advisory host event have to tolerate that either way. If you get an empty
outcome where you expected work, check that the process booted
(``hosts.boot`` / ``kernel.plugins.builtins.load_builtins``) before suspecting
the hook. Lookups that must NOT be silent (a channel descriptor, a named
service) fail loud instead — ``UnknownChannel`` / ``UnknownEntry``.
"""
from __future__ import annotations

from typing import Any


def _hooks():
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES.hooks


async def call_host_hook(event: str, /, **payload: Any):
    """Run every implementation of host ``event``; returns the HookOutcome (results in registration order).

    ``event`` is positional-only so payload keys such as ``name`` never collide with it."""
    return await _hooks().caller(event).call(**payload)


__all__ = ["call_host_hook"]
