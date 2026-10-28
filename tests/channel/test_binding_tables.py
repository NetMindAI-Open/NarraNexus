"""
@file_name: test_binding_tables.py
@author: Bin Liang
@date: 2026-09-07
@description: The agents-directory channel UNION is built with every value bound as a parameter: the channel name never lands in the SQL text, aliases are indexes, and a descriptor name that could break the query is rejected by ChannelDescriptor itself.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.channel import ChannelDescriptor, CredentialSchema
from narranexus.platform.channel.binding_tables import BindingSource, bound_channels_query, channel_binding_sources


def test_query_binds_channel_names_and_ids_as_parameters():
    sources = [
        BindingSource("lark", "(SELECT agent_id, enabled FROM channel_credentials WHERE channel = %s) AS src_0", ("lark",), "enabled"),
        BindingSource("home_assistant", "instance_homeassistant_bindings", (), None),
    ]
    query, params = bound_channels_query(sources, ["a1", "a2"])
    assert query.count(" UNION ALL ") == 1
    assert "'lark'" not in query and "src_lark" not in query
    assert params == ("lark", "lark", "a1", "a2", "home_assistant", "a1", "a2")
    assert query.count("%s") == len(params)


def test_builtin_sources_use_index_aliases():
    import narranexus.platform.module_system  # noqa: F401 — registers the builtin descriptors

    sources = channel_binding_sources()
    im = [s for s in sources if s.params]
    assert im and all(s.source.endswith(f"AS src_{i}") for i, s in enumerate(im))
    assert all(s.params == (s.channel,) for s in im)


def test_each_channel_appears_once_and_in_display_order():
    import narranexus.platform.module_system  # noqa: F401 — registers the builtin descriptors
    from narranexus.platform.channel.credential_store import all_descriptors

    channels = [s.channel for s in channel_binding_sources()]
    # home_assistant is both a registered descriptor and a binding-only table: once, from the table
    assert channels.count("home_assistant") == 1
    assert len(channels) == len(set(channels))
    im = [s.channel for s in channel_binding_sources() if s.params]
    order = {d.name: (d.ui.order if d.ui is not None else 1_000) for d in all_descriptors()}
    assert im == sorted(im, key=order.__getitem__)


def test_query_rejects_an_empty_id_list():
    with pytest.raises(ValueError, match="non-empty"):
        bound_channels_query([BindingSource("lark", "t", (), "enabled")], [])


@pytest.mark.parametrize("bad", ["lark'; DROP TABLE x; --", "Lark", "café", "a-b", "_", "_x"])
def test_descriptor_rejects_names_that_are_not_plain_identifiers(bad):
    with pytest.raises(ValueError):
        ChannelDescriptor(name=bad, display_name="x", transport="webhook", credential_schema=CredentialSchema(fields=(), supports_test=False))
