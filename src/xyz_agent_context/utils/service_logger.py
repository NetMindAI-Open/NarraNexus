"""
@file_name: service_logger.py
@author: Bin Liang
@date: 2026-03-13
@description: Shared file logger setup for background services.

Provides a single function to add a rotating file logger to any
background service (matrix-trigger, job-trigger, mcp, poller, etc.).

All logs are stored under ~/.narranexus/logs/<service_name>/.
"""

import os
import sys
from pathlib import Path

from loguru import logger


# Central log root: ~/.narranexus/logs/
LOG_ROOT = Path.home() / ".narranexus" / "logs"


def setup_service_logger(
    service_name: str,
    level: str = "DEBUG",
    rotation: str = "00:00",
    retention: str = "7 days",
) -> Path:
    """Add a rotating file logger for a background service.

    Log files are written to: ~/.narranexus/logs/<service_name>/<service_name>_YYYYMMDD.log

    The stderr (console) log level is controlled by the LOG_LEVEL environment
    variable (default: INFO).  File logs always use the *level* parameter
    (default: DEBUG) so nothing is lost on disk.

    Args:
        service_name: Name used for log directory and file prefix
            (e.g. "matrix_trigger", "job_trigger", "mcp", "module_poller").
        level: Minimum log level for **file** logs (default: DEBUG).
        rotation: When to rotate (default: daily at midnight).
        retention: How long to keep old logs (default: 7 days).

    Returns:
        Path to the log directory.
    """
    # --- stderr handler: respect LOG_LEVEL env var (default INFO) -----------
    console_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logger.remove()  # remove default DEBUG stderr handler
    logger.add(sys.stderr, level=console_level)

    # --- rotating file handler: always capture everything -------------------
    log_dir = LOG_ROOT / service_name
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = str(log_dir / f"{service_name}_{{time:YYYYMMDD}}.log")
    logger.add(
        log_file,
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
        level=level,
        rotation=rotation,
        retention=retention,
        compression="zip",
        encoding="utf-8",
        enqueue=True,
    )
    logger.info(f"Log file: {log_dir}")
    return log_dir
