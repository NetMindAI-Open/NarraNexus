"""
@file_name: test_bus_delivery_receipt_repository.py
@date: 2026-09-09
@description: BusDeliveryReceiptRepository — the per-(message, recipient)
delivery ledger. Covers the upsert life-cycle (insert then in-place update),
the partial update contract, the sender's view, and the resend-loop guard's
`prior_outcome` question (same content elsewhere = yes; same message = no;
different content = no).
"""

import pytest

from narranexus.platform.repository.bus_delivery_receipt_repository import (
    RECEIPT_ACCEPTED,
    RECEIPT_FAILED,
    RECEIPT_SILENT,
    BusDeliveryReceiptRepository,
    content_key,
)


@pytest.mark.asyncio
async def test_upsert_inserts_then_updates_in_place(db_client):
    repo = BusDeliveryReceiptRepository(db_client)
    await repo.upsert(
        message_id="m1", to_agent="b", channel_id="ch", from_agent="a",
        status=RECEIPT_ACCEPTED,
    )
    row = await repo.get("m1", "b")
    assert row["status"] == RECEIPT_ACCEPTED and row["attempts"] == 0
    assert row["reason"] is None

    await repo.upsert(
        message_id="m1", to_agent="b", channel_id="ch", from_agent="a",
        status=RECEIPT_FAILED, reason="boom", attempts=2,
    )
    row = await repo.get("m1", "b")
    assert (row["status"], row["reason"], row["attempts"]) == (RECEIPT_FAILED, "boom", 2)
    # Identity columns are not rewritten and there is still one row.
    assert row["from_agent"] == "a" and row["channel_id"] == "ch"
    assert len(await db_client.get(BusDeliveryReceiptRepository.TABLE, {"message_id": "m1"})) == 1


@pytest.mark.asyncio
async def test_partial_update_keeps_earlier_fields(db_client):
    repo = BusDeliveryReceiptRepository(db_client)
    await repo.upsert(
        message_id="m2", to_agent="b", channel_id="ch", from_agent="a",
        status=RECEIPT_FAILED, reason="first", attempts=1,
    )
    await repo.upsert(
        message_id="m2", to_agent="b", channel_id="ch", from_agent="a",
        status=RECEIPT_SILENT,
    )
    row = await repo.get("m2", "b")
    assert row["status"] == RECEIPT_SILENT
    assert row["reason"] == "first" and row["attempts"] == 1


@pytest.mark.asyncio
async def test_prior_outcome_matches_same_content_on_another_message_only(db_client):
    repo = BusDeliveryReceiptRepository(db_client)
    key = content_key("please  build the\nsite")
    await repo.upsert(
        message_id="m3", to_agent="b", channel_id="ch", from_agent="a",
        status=RECEIPT_SILENT, content_key=key,
    )
    # Same content, a different (later) message → the resend the guard exists for.
    def _silent(**kw):
        return repo.prior_outcome(
            status=RECEIPT_SILENT, within_seconds=3600, **kw
        )

    assert await _silent(
        channel_id="ch", to_agent="b", key=content_key("please build the site"),
        exclude_message_ids=["m4"],
    ) is True
    # The very message that was silent is not a "prior" silence for itself.
    assert await _silent(
        channel_id="ch", to_agent="b", key=key, exclude_message_ids=["m3"]
    ) is False
    # Different content, another channel, another recipient → fresh.
    assert await _silent(
        channel_id="ch", to_agent="b", key=content_key("something else"),
        exclude_message_ids=["m4"],
    ) is False
    assert await _silent(
        channel_id="other", to_agent="b", key=key, exclude_message_ids=["m4"]
    ) is False
    # A non-silent receipt with the same content does not count.
    await repo.upsert(
        message_id="m5", to_agent="c", channel_id="ch", from_agent="a",
        status=RECEIPT_ACCEPTED, content_key=key,
    )
    assert await _silent(
        channel_id="ch", to_agent="c", key=key, exclude_message_ids=["m6"]
    ) is False


@pytest.mark.asyncio
async def test_prior_outcome_expires_with_its_window(db_client, monkeypatch):
    """An outcome older than the window is not a prior: the guard is a window,
    never a permanent mute (review I1 — a daily check-in silenced once must
    still be able to wake the sender next month)."""
    from datetime import timedelta

    from narranexus.platform.utils.timezone import utc_now

    repo = BusDeliveryReceiptRepository(db_client)
    key = content_key("daily check-in: any blockers?")
    await repo.upsert(
        message_id="old", to_agent="b", channel_id="ch", from_agent="a",
        status=RECEIPT_SILENT, content_key=key,
    )
    await db_client.update(
        BusDeliveryReceiptRepository.TABLE, {"message_id": "old", "to_agent": "b"},
        {"updated_at": utc_now() - timedelta(seconds=7200)},
    )
    assert await repo.prior_outcome(
        channel_id="ch", to_agent="b", key=key, status=RECEIPT_SILENT,
        exclude_message_ids=["new"], within_seconds=3600,
    ) is False
    assert await repo.prior_outcome(
        channel_id="ch", to_agent="b", key=key, status=RECEIPT_SILENT,
        exclude_message_ids=["new"], within_seconds=10_000,
    ) is True


@pytest.mark.asyncio
async def test_cleanup_removes_only_stale_receipts(db_client):
    from datetime import timedelta

    from narranexus.platform.utils.timezone import utc_now

    repo = BusDeliveryReceiptRepository(db_client)
    await repo.upsert(message_id="stale", to_agent="b", channel_id="ch", from_agent="a", status=RECEIPT_ACCEPTED)
    await db_client.update(
        BusDeliveryReceiptRepository.TABLE, {"message_id": "stale", "to_agent": "b"},
        {"updated_at": utc_now() - timedelta(days=40)},
    )
    await repo.upsert(message_id="fresh", to_agent="b", channel_id="ch", from_agent="a", status=RECEIPT_ACCEPTED)
    assert await repo.cleanup_older_than_days(30) == 1
    assert await repo.get("stale", "b") is None
    assert await repo.get("fresh", "b") is not None


