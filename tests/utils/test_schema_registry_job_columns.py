"""
@file_name: test_schema_registry_job_columns.py
@author: Bin Liang
@date: 2026-04-21
@description: Smoke test ensuring the v2 timezone columns remain registered
on the instance_jobs table. Guards against accidental regressions from
schema_registry edits.
"""
from xyz_agent_context.utils.db.schema_registry import TABLES


def test_instance_jobs_has_v2_timezone_columns():
    table = TABLES["instance_jobs"]
    names = [c.name for c in table.columns]
    for expected in (
        "next_run_at_local",
        "next_run_tz",
        "last_run_at_local",
        "last_run_tz",
    ):
        assert expected in names, f"column {expected!r} missing from instance_jobs"


def test_instance_jobs_utc_columns_untouched():
    """Ensure we did not accidentally drop the poller-facing UTC columns."""
    table = TABLES["instance_jobs"]
    names = [c.name for c in table.columns]
    assert "next_run_time" in names
    assert "last_run_time" in names
