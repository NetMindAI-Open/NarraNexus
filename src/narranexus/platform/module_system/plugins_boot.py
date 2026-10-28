"""
@file_name: plugins_boot.py
@author: Bin Liang
@date: 2026-09-03
@description: Plugin boot for the agent-side processes (mcp module servers, workers supervisor).

Health is NOT declared here. ``BootReport.mark_healthy`` clears the boot-crash
counter and moves the last-known-good snapshot, so it may only fire once the
process actually serves — these hosts call ``mark_host_healthy(role)`` from
their entrypoint after the server / supervisor is up. Marking it at the end of
``boot()`` meant a process that booted the plugins and then died before serving
still advanced the rollback target, which is precisely the state safe mode
exists to escape.

These hosts register declarative contributions only: tools, MCP servers,
skills, workers. They never activate user plugin code (activation needs the
backend's context: settings store, service locator, event bus) and never
register plugin tables (the backend owns migrations). The boot marker is per
role, so an mcp crash loop flips safe mode for mcp without touching the
backend's counter.
"""
from __future__ import annotations

from narranexus.hosts.boot import BootReport, Role, boot
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.plugins.compat import host_version
from narranexus.kernel.plugins.distribution import resolve_from_env
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

_REPORTS: dict[str, BootReport] = {}


def _boot(role: Role, *, inspect: bool = False) -> BootReport:
    if role in _REPORTS:
        return _REPORTS[role]
    if KERNEL_REGISTRIES.frozen:
        # Already booted in this process (tests, or a second runner call).
        return _REPORTS.setdefault(role, BootReport(role=role))
    report = boot(
        role, registries=KERNEL_REGISTRIES, cloud=is_cloud_mode(), host_version=host_version(),
        distribution=resolve_from_env(host_version=host_version()), inspect=inspect,
    )
    from narranexus.platform.bindings_runtime import resolve_runtime_bindings

    resolve_runtime_bindings(resolve_from_env(host_version=host_version()), snapshot=False)
    _REPORTS[role] = report
    return report


def mark_host_healthy(role: Role) -> None:
    """The entrypoint's health signal: called AFTER the host is serving.

    Idempotent and never raises — a host that calls it twice (a supervisor that
    restarts its workers) just re-snapshots, and a host that never reaches it
    leaves the boot marker in place, which is what turns a crash loop into
    safe mode.
    """
    report = _REPORTS.get(role)
    if report is not None:
        report.mark_healthy()


def boot_mcp_plugins() -> BootReport:
    return _boot("mcp")


def boot_worker_plugins() -> BootReport:
    return _boot("workers")


def boot_executor_plugins() -> BootReport:
    """The per-user executor runs agent turns (frameworks, providers, clients,
    memory kinds, prompt sections, turn strategies, modules) — the same
    contribution set the workers process needs, so it boots the ``workers``
    role. Before this the executor never booted and relied on lazy
    self-registration inside every platform seam."""
    return _boot("workers")


def boot_turn_plugins() -> BootReport:
    """Boot for a process that lives ONE turn (the local NexusPower runner).

    Same contribution set as the executor (``workers`` role) but read-only:
    no boot marker and no registry writes. The marker is a crash-loop counter
    for long-lived hosts — a process that exits by design never reaches
    ``mark_host_healthy``, so every turn it ran counted as a failed workers
    boot and the third conversation put the whole app into SAFE MODE
    (2026-09-08 local plugin-factory e2e; the CLI had the same bug earlier).
    """
    return _boot("workers", inspect=True)


def boot_channel_plugins() -> BootReport:
    """The standalone channels supervisor (cloud ``--only channels`` layout) hosts
    the same trigger set the workers process does, so it boots the ``workers`` role."""
    return _boot("workers")


__all__ = ["boot_channel_plugins", "boot_executor_plugins", "boot_mcp_plugins", "boot_worker_plugins", "mark_host_healthy"]
