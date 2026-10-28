"""
@file_name: test_unrestricted_web_access.py
@date: 2026-09-23
@description: Retired website rules cannot block browsing or be recreated.
"""
import json

import pytest

from narranexus.platform.browser._browser_impl.policy import BrowserPolicy
from narranexus.platform.browser._browser_impl.policy_store import PolicyStore


@pytest.mark.asyncio
async def test_legacy_access_rules_are_absent_from_policy_and_public_view(db_client):
    document = {
        "default_origin_policy": {"access": "deny"},
        "origins": {
            "https://blocked.example": {"access": "deny"},
            "https://scripts.example": {"access": "ask", "full_cdp_access": "allow"},
        },
        "grants": [["https://allowed.example", "access", "thread:old"]],
        "denials": [["https://blocked.example", "access", "turn:old"]],
    }
    await db_client.insert("instance_browser_policies", {"agent_id": "agent", "policy_json": json.dumps(document)})
    policy = BrowserPolicy.from_dict(document).to_dict()
    assert "access" not in policy["default_origin_policy"]
    assert "https://blocked.example" not in policy["origins"]
    assert "grants" not in policy and "denials" not in policy
    assert await PolicyStore(db_client).get_public("agent") == {
        "agent_id": "agent", "defaults": {"full_cdp_access": "deny"},
        "origins": [{"origin": "https://scripts.example", "full_cdp_access": "allow"}],
    }


@pytest.mark.asyncio
async def test_access_rules_can_no_longer_be_created(db_client):
    with pytest.raises(ValueError):
        await PolicyStore(db_client).set_rule("agent", origin="https://site.example", capability="access", verdict="deny")
    assert await db_client.get("instance_browser_policies", {"agent_id": "agent"}) == []
