"""
@file_name: test_unbooted_process_fails_loud.py
@author: Bin Liang
@date: 2026-09-07
@description: What the four platform seams answer in a process that never booted the plugin platform — one test per call site that used to carry the deleted "import module_system and the builtins are guaranteed to exist" line.

Five call sites imported ``narranexus.platform.module_system`` with the comment
*"registers the builtin descriptors / hooks / services (idempotent)"*. That
guarantee died with ``register_all``: registration happens only at boot now, so
the import registered nothing and the comment described a contract the code no
longer had. Deleting the imports must not change WHEN a failure surfaces — only
stop lying about it — so each seam is pinned here against a PRIVATE, unbooted
``Registries()``:

* ``descriptor_for`` / ``CHANNELS`` — loud ``UnknownChannel``, never an
  implicit "sure, that channel exists".
* the service locator — loud ``UnknownEntry``, the same answer as "the owning
  builtin is disabled", which is the point.
* the hook registry — an EMPTY outcome, not an exception. That is the one seam
  whose unbooted answer is genuinely silent, and it is silent by design (an
  advisory host event whose implementations are all disabled answers the same
  way), so the test states it rather than pretending otherwise. ``host_hooks``'
  docstring now says exactly this.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import UnknownEntry
from narranexus.kernel.plugins.registries import Registries


def test_descriptor_for_on_an_unbooted_registry_raises_unknown_channel():
    from narranexus.platform.channel.credential_store import UnknownChannel, descriptor_for

    with pytest.raises(UnknownChannel):
        descriptor_for("lark", Registries())


def test_all_descriptors_on_an_unbooted_registry_is_empty_not_wrong():
    """An empty tuple is the honest answer: this process knows of no channel.

    Paired with the test above so "empty" can never be mistaken for "and every
    lookup still works" — the lookup raises."""
    from narranexus.platform.channel.credential_store import all_descriptors

    assert all_descriptors(Registries()) == ()


def test_channel_specs_on_an_unbooted_registry_are_empty_and_lookups_raise():
    """``SUPPORTED_CHANNELS`` is consulted per HTTP request
    (``backend/routes/agents/channel_credentials.py``); on an unbooted process it
    is empty and a bind attempt 404s rather than reaching a half-built store."""
    from narranexus.platform.module_system.data_access.channel_store import _ChannelSpecs

    specs = _ChannelSpecs(Registries())
    assert len(specs) == 0
    assert "lark" not in specs
    with pytest.raises(KeyError):
        specs["lark"]


def test_service_locator_on_an_unbooted_registry_raises_unknown_entry():
    from narranexus.contracts.services import SKILL_WORKSPACES

    services = Registries().services
    with pytest.raises(UnknownEntry):
        services.require(SKILL_WORKSPACES)
    # The degraded-path variant the platform's ``try_*`` wrappers use.
    assert services.try_require(SKILL_WORKSPACES) is None


@pytest.mark.asyncio
async def test_host_hooks_on_an_unbooted_registry_run_nothing_and_do_not_raise():
    """The documented truth for ``call_host_hook``: zero implementations, no error.

    Deliberately NOT converted into a raise — that would change when failures
    surface for every advisory host event, including the legitimate case where a
    distribution ships no implementation of it."""
    from narranexus.contracts.events import HOST_EVENTS, host_event_params

    hooks = Registries().hooks
    event = sorted(HOST_EVENTS)[0]
    payload = {name: None for name in host_event_params(event)}
    outcome = await hooks.caller(event).call(**payload)
    assert list(outcome.results) == []
