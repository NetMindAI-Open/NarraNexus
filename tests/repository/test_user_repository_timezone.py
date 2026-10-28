"""
@file_name: test_user_repository_timezone.py
@date: 2026-08-18
@description: `get_user_timezone` must always return a USABLE IANA zone.

The bug this guards against is one the codebase already had once: the
docstring promised "returns 'UTC' if user does not exist" while the
implementation returned `user.timezone` verbatim for any user that DID
exist — blank and malformed included. The promise was therefore kept only
by each caller remembering to wrap the result in `resolve_timezone`, and
three of six had not.

Most of those got away with it because something downstream wrapped the
value again. `services/instance_sync_service` did not: it freezes this
value into a Job's `trigger_config` at creation, so a malformed zone is
PERSISTED and re-read on every later schedule computation, where it
reaches `ZoneInfo()` and raises. The symptom is "some jobs stopped
firing", months after whatever wrote the bad row.

Why these tests exist at all, given the current write paths are clean:
`backend/routes/auth.py` validates with `is_valid_timezone`, `add_user`
defaults to "UTC", and the schema default is "UTC" — so a bad value can
only arrive from a historical row, a direct DB edit, or a write path
nobody has added yet. The contract is therefore not currently load-bearing
for any user; it is load-bearing for the NEXT change. Without these
assertions, someone cleaning up the repository layer (or deciding the
`resolve_timezone` wrapper is redundant overhead) reverts one line, CI
stays green, and the persisted-bad-timezone failure comes back.

Verified by mutation: restoring the old `if user: return user.timezone`
leaves every other test in the suite green.

The assertions are exact `"UTC"` literals on purpose. `is_valid_timezone(...)`
would be satisfied by the old implementation too on the cases that matter,
i.e. would test nothing.
"""

import pytest

from narranexus.platform.repository import UserRepository


@pytest.mark.asyncio
async def test_unknown_user_gets_utc(db_client):
    assert await UserRepository(db_client).get_user_timezone("nobody_at_all") == "UTC"


@pytest.mark.asyncio
async def test_valid_zone_passes_through_untouched(db_client):
    repo = UserRepository(db_client)
    await repo.add_user(
        user_id="tz_valid", user_type="individual", timezone="Asia/Shanghai"
    )
    assert await repo.get_user_timezone("tz_valid") == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_blank_stored_zone_degrades_to_utc(db_client):
    repo = UserRepository(db_client)
    await repo.add_user(user_id="tz_blank", user_type="individual", timezone="")

    # Confirm the blank actually reached the row. Pydantic's Field(default=
    # "UTC") only fires when the field is ABSENT, so an explicit "" persists —
    # but if that ever changed, this test would silently degrade into
    # "asserts UTC passes through UTC" and stop guarding anything.
    stored = await repo.get_user("tz_blank")
    assert stored is not None and stored.timezone == "", (
        "fixture no longer stores a blank timezone; this test would be vacuous"
    )

    assert await repo.get_user_timezone("tz_blank") == "UTC"


@pytest.mark.asyncio
async def test_malformed_stored_zone_degrades_to_utc(db_client):
    """The value is written straight to the row, bypassing the route.

    `backend/routes/auth.py` rejects this with `is_valid_timezone` — which
    is precisely why the case has to be simulated at the storage layer: what
    we are modelling is a value that is ALREADY in the table.
    """
    repo = UserRepository(db_client)
    await repo.add_user(user_id="tz_bad", user_type="individual")
    await repo.update_user("tz_bad", {"timezone": "GMT+8"})  # not an IANA name

    stored = await repo.get_user("tz_bad")
    assert stored is not None and stored.timezone == "GMT+8"

    assert await repo.get_user_timezone("tz_bad") == "UTC"


@pytest.mark.asyncio
async def test_result_is_always_directly_usable_as_a_zoneinfo(db_client):
    """The property the callers actually depend on: whatever comes back can
    be handed to ZoneInfo() without a guard. That is what lets
    instance_sync_service freeze it into a job."""
    from zoneinfo import ZoneInfo

    repo = UserRepository(db_client)
    await repo.add_user(user_id="tz_p1", user_type="individual", timezone="")
    await repo.add_user(user_id="tz_p2", user_type="individual", timezone="Asia/Tokyo")
    await repo.add_user(user_id="tz_p3", user_type="individual")
    await repo.update_user("tz_p3", {"timezone": "Mars/Olympus"})

    for user_id in ("tz_p1", "tz_p2", "tz_p3", "no_such_user"):
        ZoneInfo(await repo.get_user_timezone(user_id))  # must not raise
