"""
@file_name: catalog.py
@author: Bin Liang
@date: 2026-09-07
@description: ``GET /api/plugins/channels`` — the channel catalog the shell renders, read from the ``ingress.channels`` registry.

Why it exists: the frontend used to carry its own six-row table
(``registerBuiltinChannels.ts``) that restated, by hand, the ``label`` /
``icon`` / ``order`` each Python ``ChannelUi`` already declares. Two copies of
one fact, in two languages, kept in step by nobody — and a third-party channel
plugin could ship a perfect descriptor and still not appear in the Channels
page, because appearing there meant editing a file in the engine's frontend.

It lives under ``/api/plugins`` rather than ``/api/channels`` because that is
what it is: a view of the plugin registry, not an operation on one channel. The
``/api/channels/{channel}/…`` routes in ``generic.py`` act on an agent's binding
and every one of them is ownership-gated; putting an ownership-free listing in
the same namespace would invite the next reader to copy the wrong neighbour.

This endpoint is the seam: one row per registered descriptor, so a channel that
this distribution excludes or ``registry.json`` disables is simply absent, and a
plugin channel is present the moment it is installed. It is **ownership-free**:
the catalog is the set of channels this deployment ships, not anything about the
caller's agents — the same answer for every authenticated user, exactly like
``/api/channels/{channel}/schema`` next door. Credentials, bindings and the
per-agent state stay on the ownership-checked routes in ``generic.py``.

It carries no secrets: name, display name, the three presentation fields, and
the owning plugin id (which the UI uses to attribute a channel to the plugin
that provides it).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from loguru import logger

router = APIRouter()


def _row(name: str, descriptor: Any, owner: str) -> dict[str, Any]:
    ui = descriptor.ui
    return {
        "name": descriptor.name,
        "display_name": descriptor.display_name,
        "owner": owner,
        "ui": {
            # A descriptor with no ChannelUi still renders: the display name is
            # the label, and a channel that stated no order sorts last rather
            # than jumping to the front of the strip.
            "label": ui.label if ui is not None else descriptor.display_name,
            "icon": ui.icon if ui is not None else "message-square",
            "order": ui.order if ui is not None else 100,
        },
    }


@router.get("/channels")
async def channel_catalog() -> dict[str, Any]:
    """Every channel in ``ingress.channels``, in ``ui.order`` then name order.

    Per-entry isolation, fail-closed, the policy every other channel view uses
    (``_ChannelSpecs._build`` / ``TriggerMapView._build``): one descriptor that
    cannot be built is warned about and skipped, so it loses ITS row instead of
    emptying the page.
    """
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    rows: list[dict[str, Any]] = []
    for entry in KERNEL_REGISTRIES.registry_for("ingress.channels").entries():
        try:
            rows.append(_row(entry.name, entry.factory(), entry.owner))
        except Exception as e:  # noqa: BLE001 — one broken descriptor must not hide every channel
            logger.warning(f"channel descriptor {entry.name!r} unavailable, omitted from the catalog ({type(e).__name__}: {e})")
    rows.sort(key=lambda r: (r["ui"]["order"], r["name"]))
    return {"success": True, "data": rows}
