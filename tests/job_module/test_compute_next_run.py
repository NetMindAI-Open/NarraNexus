"""
@file_name: test_compute_next_run.py
@author: Bin Liang
@date: 2026-04-21
@description: Tests for compute_next_run() - the single atomic point that produces
the (local, tz, utc) triple from a TriggerConfig.
"""
from datetime import datetime, timezone as dt_tz
import pytest

from narranexus.platform.schema.job_schema import TriggerConfig, JobType
from narranexus.platform.utils.job_scheduling import (
    compute_next_run,
    NextRunTuple,
)


class TestOneOff:
    def test_shanghai_morning(self):
        trigger = TriggerConfig(
            run_at=datetime(2026, 5, 1, 8, 0, 0),
            timezone="Asia/Shanghai",
        )
        result = compute_next_run(JobType.ONE_OFF, trigger)
        assert isinstance(result, NextRunTuple)
        assert result.local == "2026-05-01T08:00:00"
        assert result.tz == "Asia/Shanghai"
        # 8am Shanghai = 0am UTC
        assert result.utc == datetime(2026, 5, 1, 0, 0, 0, tzinfo=dt_tz.utc)


class TestCronScheduled:
    def test_every_day_8am_shanghai(self):
        trigger = TriggerConfig(cron="0 8 * * *", timezone="Asia/Shanghai")
        base = datetime(2026, 5, 1, 7, 0, 0, tzinfo=dt_tz.utc)  # 15:00 Shanghai
        result = compute_next_run(JobType.SCHEDULED, trigger, last_run_utc=base)
        # next 8am Shanghai after 15:00 Shanghai = next day 8am Shanghai = 0am UTC
        assert result.utc == datetime(2026, 5, 2, 0, 0, 0, tzinfo=dt_tz.utc)
        assert result.local == "2026-05-02T08:00:00"
        assert result.tz == "Asia/Shanghai"

    def test_dst_transition_new_york(self):
        # EDT -> EST around 2025-11-02 02:00 local
        trigger = TriggerConfig(cron="0 8 * * *", timezone="America/New_York")
        base = datetime(2025, 11, 1, 13, 0, 0, tzinfo=dt_tz.utc)  # 9am EDT
        result = compute_next_run(JobType.SCHEDULED, trigger, last_run_utc=base)
        assert result.tz == "America/New_York"
        # next 8am in NY after 9am 2025-11-01 EDT is 2025-11-02 8am EST = 13:00 UTC
        assert result.utc == datetime(2025, 11, 2, 13, 0, 0, tzinfo=dt_tz.utc)
        assert result.local == "2025-11-02T08:00:00"


class TestIntervalScheduled:
    def test_hourly(self):
        trigger = TriggerConfig(interval_seconds=3600, timezone="Asia/Shanghai")
        base = datetime(2026, 5, 1, 0, 0, 0, tzinfo=dt_tz.utc)  # 8am Shanghai
        result = compute_next_run(JobType.SCHEDULED, trigger, last_run_utc=base)
        assert result.utc == datetime(2026, 5, 1, 1, 0, 0, tzinfo=dt_tz.utc)
        assert result.local == "2026-05-01T09:00:00"
        assert result.tz == "Asia/Shanghai"


class TestOngoing:
    def test_interval(self):
        trigger = TriggerConfig(
            interval_seconds=86400,
            end_condition="x",
            timezone="Asia/Shanghai",
        )
        base = datetime(2026, 5, 1, 0, 0, 0, tzinfo=dt_tz.utc)
        result = compute_next_run(JobType.ONGOING, trigger, last_run_utc=base)
        assert result.utc == datetime(2026, 5, 2, 0, 0, 0, tzinfo=dt_tz.utc)


class TestOneOffNoBase:
    def test_does_not_need_last_run(self):
        trigger = TriggerConfig(
            run_at=datetime(2026, 5, 1, 8, 0, 0),
            timezone="Asia/Shanghai",
        )
        result = compute_next_run(JobType.ONE_OFF, trigger)
        assert result is not None
