"""
@file_name: test_me_network_merge_key.py
@author:
@date: 2026-08-12
@description: Pin the /me/network merge key (Mark's item 11).

Two different real people sharing a common name (e.g. two "王小明") were
collapsed into one node because the merge key used only entity_type + name.
The fix adds a stable cross-agent identity signal (email/phone from
contact_info) to the key. Same person seen by N agents (same contact, or no
contact at all) must still merge.
"""
from __future__ import annotations

from backend.routes.me import _entity_key


def test_same_name_different_email_do_not_merge():
    a = {"entity_name": "王小明", "contact_info": {"email": "eng@example.com"}}
    b = {"entity_name": "王小明", "contact_info": {"email": "chef@example.com"}}
    assert _entity_key("user", a) != _entity_key("user", b)


def test_same_name_same_email_merge():
    a = {"entity_name": "王小明", "contact_info": {"email": "eng@example.com"}}
    b = {"entity_name": "王小明", "contact_info": {"email": "eng@example.com"}}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_same_name_no_contact_still_merges():
    a = {"entity_name": "kz"}
    b = {"entity_name": "kz"}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_same_name_different_slack_only_do_not_merge():
    # contact_info is LLM-authored — a person may be known only by slack/wechat/
    # telegram, not email/phone. Two distinct people must still separate.
    a = {"entity_name": "王小明", "contact_info": {"slack": "@dana"}}
    b = {"entity_name": "王小明", "contact_info": {"slack": "@dave"}}
    assert _entity_key("user", a) != _entity_key("user", b)


def test_same_name_same_slack_only_merge():
    a = {"entity_name": "王小明", "contact_info": {"slack": "@dana"}}
    b = {"entity_name": "王小明", "contact_info": {"slack": "@dana"}}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_email_wins_over_other_fields_regardless_of_dict_order():
    # Same person seen by two agents; each dict lists fields in a different order.
    # The key must not depend on insertion order, or one person splits into N.
    a = {"entity_name": "x", "contact_info": {"email": "e@x.com", "slack": "@s"}}
    b = {"entity_name": "x", "contact_info": {"slack": "@s", "email": "e@x.com"}}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_fallback_is_deterministic_across_dict_order():
    a = {"entity_name": "x", "contact_info": {"slack": "@s", "wechat": "w"}}
    b = {"entity_name": "x", "contact_info": {"wechat": "w", "slack": "@s"}}
    assert _entity_key("user", a) == _entity_key("user", b)


def test_non_string_contact_values_do_not_crash():
    # attrs is LLM-authored JSON — phone may be an int, email a list, and the
    # /network merge loop has no try/except, so a raise here would 500 the graph.
    assert _entity_key("user", {"entity_name": "x", "contact_info": {"phone": 13800138000}})
    assert _entity_key("user", {"entity_name": "x", "contact_info": {"email": ["a@x.com"]}})
    assert _entity_key("user", {"entity_name": "x", "contact_info": "not-a-dict"})
