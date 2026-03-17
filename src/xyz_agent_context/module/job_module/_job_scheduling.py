"""
@file_name: _job_scheduling.py
@author: Bin Liang
@date: 2026-03-06
@description: Job scheduling utility functions

Extracted from job_repository.py. Contains business logic for
calculating next execution times based on job type and trigger config.

This logic belongs in the module layer (not repository) because it
encodes scheduling rules rather than data access patterns.
"""

from datetime import datetime, timedelta
from typing import Optional

from loguru import logger

from xyz_agent_context.utils import utc_now
from xyz_agent_context.schema.job_schema import JobType, TriggerConfig


def calculate_next_run_time(
    job_type: JobType,
    trigger_config: TriggerConfig,
    last_run_time: Optional[datetime] = None
) -> Optional[datetime]:
    """
    Calculate the next execution time for a task

    Supports four trigger types:
    1. ONE_OFF + run_at: Execute at a specified time
    2. SCHEDULED + cron: Execute by cron expression (requires croniter)
    3. SCHEDULED + interval_seconds: Execute at fixed intervals
    4. ONGOING + interval_seconds: Execute at fixed intervals until end condition is met

    Args:
        job_type: Task type (ONE_OFF, SCHEDULED, or ONGOING)
        trigger_config: Trigger configuration object
        last_run_time: Last execution time (used for interval calculation)

    Returns:
        Next execution time, or None if not applicable

    Example:
        # One-off task
        next_time = calculate_next_run_time(
            JobType.ONE_OFF,
            TriggerConfig(run_at=datetime(2025, 1, 16, 8, 0))
        )

        # Cron task (daily at 8 AM)
        next_time = calculate_next_run_time(
            JobType.SCHEDULED,
            TriggerConfig(cron="0 8 * * *")
        )

        # Interval task (every hour)
        next_time = calculate_next_run_time(
            JobType.SCHEDULED,
            TriggerConfig(interval_seconds=3600),
            last_run_time=utc_now()
        )

        # ONGOING task (check every hour until condition is met)
        next_time = calculate_next_run_time(
            JobType.ONGOING,
            TriggerConfig(interval_seconds=3600, end_condition="Customer completes purchase"),
            last_run_time=utc_now()
        )
    """
    if job_type == JobType.ONE_OFF:
        return trigger_config.run_at

    elif job_type == JobType.SCHEDULED:
        if trigger_config.cron:
            try:
                from croniter import croniter
                base_time = last_run_time or utc_now()
                cron = croniter(trigger_config.cron, base_time)
                return cron.get_next(datetime)
            except ImportError:
                logger.warning(
                    "croniter package not installed. "
                    "Install with: pip install croniter"
                )
                return utc_now() + timedelta(hours=1)
            except Exception as e:
                logger.error(f"Failed to parse cron expression '{trigger_config.cron}': {e}")
                return None

        elif trigger_config.interval_seconds:
            base_time = last_run_time or utc_now()
            return base_time + timedelta(seconds=trigger_config.interval_seconds)

    elif job_type == JobType.ONGOING:
        if trigger_config.interval_seconds:
            base_time = last_run_time or utc_now()
            return base_time + timedelta(seconds=trigger_config.interval_seconds)

    return None
