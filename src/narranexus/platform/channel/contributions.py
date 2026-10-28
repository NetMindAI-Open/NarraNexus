"""
@file_name: contributions.py
@author: Bin Liang
@date: 2026-09-07
@description: One ChannelDescriptor → the plugin's module and trigger contributions, and the one place a channel's WorkingSource is registered. A channel plugin writes its descriptor once; its ``contribution.py`` derives MODULES / TRIGGERS from it instead of restating the class refs in three places.

``contributions_from`` is called exactly once per channel plugin, from that
plugin's ``contribution.py``, which the manifest loader imports at BOOT while
resolving the plugin's ``provides`` — and only for a plugin this distribution
installs and ``registry.json`` has not disabled. That makes it the honest home
for the channel's ``WorkingSource`` (and its ``TriggerType`` twin) registration:

* not ``descriptor.py``'s old module-level ``SOURCE = WorkingSource.register(...)``,
  which fired for anything that merely imported the package;
* and above all not a READ path — ``_ChannelSpecs._build`` and
  ``TriggerMapView._build`` both used to register from a cache miss, so whether
  ``WorkingSource("lark")`` resolved depended on whether anyone had happened to
  read ``SUPPORTED_CHANNELS`` yet (a membership test on an HTTP request path).
"""
from __future__ import annotations

import importlib
from typing import Any, Callable

from narranexus.contracts.channel import ChannelDescriptor, MessageSourceSpec
from narranexus.contracts.trigger import TriggerSpec
from narranexus.kernel.plugins.registry import Contribution


def _loader(ref: str) -> Callable[[], Any]:
    module_path, attr = ref.rsplit(":", 1)
    return lambda: getattr(importlib.import_module(module_path), attr)


def module_contribution(class_ref: str, plugin_id: str, *, channel: bool) -> Contribution[type]:
    """``agent.capabilities.modules`` entry for a module class named ``pkg.mod:Class`` (imported lazily)."""
    return Contribution(class_ref.rsplit(":", 1)[1], _loader(class_ref), meta={"plugin_id": plugin_id, "channel": channel})


def trigger_contribution(spec: TriggerSpec) -> Contribution[TriggerSpec]:
    """``ingress.triggers`` entry carrying the spec (the class is resolved by the consumer, per channel, so one missing dependency isolates one channel)."""
    return Contribution(spec.name, lambda: spec, meta={"host": spec.host, "class_ref": spec.class_ref})


def register_working_source(descriptor: ChannelDescriptor) -> None:
    """Register the channel's inbound ``WorkingSource`` (and its ``TriggerType`` twin).

    Idempotent, and deliberately a single call in a single place: the twin
    registration must stay one operation (``narrative/models.py`` documents the
    pairing — splitting it leaves the channel's events unlabelled). Only a channel
    that can actually receive gets a source."""
    if not descriptor.has_inbound:
        return
    from narranexus.platform.schema.hook_schema import WorkingSource

    WorkingSource.register(descriptor.name)


def contributions_from(descriptor: ChannelDescriptor, plugin_id: str) -> tuple[tuple[Contribution[type], ...], tuple[Contribution[TriggerSpec], ...]]:
    """(MODULES, TRIGGERS) a channel plugin contributes, derived from its descriptor.

    Also registers the channel's ``WorkingSource`` — see the module docstring for
    why this is the one place that happens. The channel's MESSAGE SOURCE needs no
    contribution: it is projected from the descriptor already in
    ``ingress.channels`` (``ChannelDescriptor.message_source``)."""
    register_working_source(descriptor)
    modules = (module_contribution(descriptor.module_ref, plugin_id, channel=True),) if descriptor.module_ref else ()
    triggers = (trigger_contribution(TriggerSpec(descriptor.name, descriptor.trigger_ref)),) if descriptor.trigger_ref else ()
    return modules, triggers


def message_source_contribution(spec: MessageSourceSpec) -> Contribution[MessageSourceSpec]:
    """``ingress.message_sources`` entry for a source that is NOT a channel (the bus, the job clock)."""
    return Contribution(spec.name, lambda: spec)


__all__ = ["contributions_from", "message_source_contribution", "module_contribution", "register_working_source", "trigger_contribution"]
