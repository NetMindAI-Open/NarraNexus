"""
@file_name: test_cloud_framework_policy.py
@author: Bin Liang
@date: 2026-07-29
@description: Which agent frameworks a cloud non-staff user may select.

The gate exists for CREDENTIAL RIDING, not framework variety: the
CLI-backed frameworks authenticate through a credential file in the cloud
image's shared HOME, so a non-staff user selecting one would run on
staff's login. A framework that drives the provider API with the key of
the card bound to the agent slot carries no such risk.

Guarded here because the rule was previously written as
``framework != "claude_code"`` in three places, and when NexusPower became
cloud-legal all three kept rejecting it.
"""

import pytest

from xyz_agent_context.agent_framework.providers.cloud_policy import (
    CLOUD_ALLOWED_FRAMEWORKS,
    framework_allowed_in_cloud,
)
from xyz_agent_context.schema.provider_schema import (
    ProviderProtocol,
    get_slot_required_protocols,
)


@pytest.fixture()
def cloud(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.providers.cloud_policy.is_cloud_mode",
        lambda: True,
    )


@pytest.fixture()
def local(monkeypatch):
    monkeypatch.setattr(
        "xyz_agent_context.agent_framework.providers.cloud_policy.is_cloud_mode",
        lambda: False,
    )


def test_cloud_nonstaff_may_pick_nexus_power(cloud):
    """The reason this test exists: it drives the provider API with the
    user's own bound key and refuses OAuth, so it cannot ride staff's
    shared CLI login."""
    assert framework_allowed_in_cloud("nexus_power", False) is True


def test_cloud_nonstaff_may_still_pick_claude_code(cloud):
    """Cloud provisions an API-key NetMind card for it; the staff-only
    thing is the OAuth CARD, not this framework."""
    assert framework_allowed_in_cloud("claude_code", False) is True


def test_cloud_nonstaff_may_not_pick_a_cli_credential_framework(cloud):
    assert framework_allowed_in_cloud("codex_cli", False) is False


def test_cloud_staff_keeps_full_choice(cloud):
    for fw in ("claude_code", "codex_cli", "nexus_power", "something_new"):
        assert framework_allowed_in_cloud(fw, True) is True


def test_local_is_never_gated(local):
    for staff in (True, False):
        assert framework_allowed_in_cloud("codex_cli", staff) is True


def test_unknown_framework_is_refused_in_cloud(cloud):
    """Fail closed: a framework nobody has classified must not slip
    through just because it is new."""
    assert framework_allowed_in_cloud("brand_new_thing", False) is False
    assert "brand_new_thing" not in CLOUD_ALLOWED_FRAMEWORKS


def test_nexus_power_agent_slot_accepts_both_protocols():
    """NexusPower drives the provider API itself, so either protocol
    binds. Absent from the table it fell back to claude_code's
    anthropic-only rule, which rejected every openai card at BIND time
    while the resolver handled them fine."""
    allowed = get_slot_required_protocols("agent", agent_framework="nexus_power")
    assert set(allowed) == {ProviderProtocol.ANTHROPIC, ProviderProtocol.OPENAI}


def test_cli_backed_frameworks_stay_single_protocol():
    assert get_slot_required_protocols("agent", agent_framework="claude_code") == [
        ProviderProtocol.ANTHROPIC
    ]
    assert get_slot_required_protocols("agent", agent_framework="codex_cli") == [
        ProviderProtocol.OPENAI
    ]


# --- the per-agent pin composition (slot_service) -------------------------
#
# Two conditions multiply there, and dropping either one has bitten:
#   * gate on the TARGET framework — keying off "differs from the owner's"
#     locked cloud users out of every framework the policy allows;
#   * except when they already run it — refusing that blocked every
#     agent-slot edit a legacy codex_cli user makes, while closing nothing.


class _Db:
    """Minimal async DB double: agents + user_slots + agent_slots."""

    def __init__(self, owner_framework: str, source: str = "netmind",
                 protocol: str = "openai") -> None:
        self._owner_framework = owner_framework
        self._source = source
        self._protocol = protocol
        self.inserted: list[dict] = []

    async def get_one(self, table, filters):
        if table == "agents":
            return {"agent_id": "ag1", "created_by": "u1"}
        if table == "user_slots":
            return {"agent_framework": self._owner_framework}
        if table == "user_providers":
            return {
                "provider_id": filters.get("provider_id"),
                "source": self._source,
                "protocol": self._protocol,
            }
        return None

    async def get(self, table, filters):
        return []

    async def insert(self, table, row):
        self.inserted.append(row)

    async def update(self, table, filters, payload):  # pragma: no cover
        self.inserted.append(payload)


async def _pin(owner_framework: str, pinned: str, **kw):
    from xyz_agent_context.agent_framework.providers.slot_service import (
        AgentSlotService,
    )

    db = _Db(owner_framework, **kw)
    service = AgentSlotService(db)
    await service.set_agent_slot(
        "ag1", "agent", "p1", "some-model",
        agent_framework=pinned, actor_is_staff=False,
    )
    return db


@pytest.mark.asyncio
async def test_pin_nexus_power_is_allowed_in_cloud(cloud):
    db = await _pin("claude_code", "nexus_power")
    assert db.inserted and db.inserted[0]["agent_framework"] == "nexus_power"


@pytest.mark.asyncio
async def test_pin_cli_framework_is_refused_in_cloud(cloud):
    from xyz_agent_context.agent_framework.providers.cloud_policy import (
        CloudPolicyViolation,
    )

    with pytest.raises(CloudPolicyViolation):
        await _pin("claude_code", "codex_cli")


@pytest.mark.asyncio
async def test_pin_the_framework_already_in_use_stays_allowed(cloud):
    """A legacy codex_cli owner gains no new exposure by pinning codex_cli;
    refusing it would answer a model change with a framework error."""
    db = await _pin("codex_cli", "codex_cli")
    assert db.inserted and db.inserted[0]["agent_framework"] == "codex_cli"
