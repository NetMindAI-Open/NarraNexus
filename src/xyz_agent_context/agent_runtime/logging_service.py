"""
@file_name: logging_service.py
@author: NetMind.AI
@date: 2025-11-28
@description: Agent runtime logging service

Logging management module extracted from AgentRuntime, responsible for configuring and managing logs during Agent execution.

Design principles:
- Single responsibility: only responsible for log configuration and management
- Context manager support: automatic setup and cleanup of log handlers
- Configurable: supports different log directories and configurations

Key implementation details:
- Must use loguru's {time} placeholder for filename generation, not manual timestamp concatenation.
  loguru's retention mechanism discovers old files by replacing {time} with .* for glob matching.
  Manually concatenated timestamps are treated as fixed strings, causing retention to never match historical files.
"""

from pathlib import Path
from typing import Optional
from contextlib import contextmanager
from loguru import logger


class LoggingService:
    """
    Agent runtime logging service

    Manages file logging configuration during Agent execution.
    Uses loguru's {time} placeholder for filename generation, ensuring retention can correctly match and clean up old files.

    Usage:
        # Method 1: Using context manager (recommended)
        >>> logging_svc = LoggingService()
        >>> with logging_svc.session(agent_id="agent_123"):
        ...     logger.info("This will be logged to file")

        # Method 2: Manual management
        >>> logging_svc = LoggingService()
        >>> logging_svc.setup(agent_id="agent_123")
        >>> try:
        ...     logger.info("This will be logged to file")
        ... finally:
        ...     logging_svc.cleanup()
    """

    def __init__(
        self,
        log_dir: Optional[str] = None,
        enabled: bool = True,
        log_level: str = "DEBUG",
        retention: str = "30 days",
        compression: str = "zip",
    ):
        """
        Initialize logging service

        Args:
            log_dir: Log directory path, defaults to the logs folder under the project root
            enabled: Whether to enable file logging
            log_level: Log level
            retention: Log retention period (expired files are cleaned up when the sink is closed)
            compression: Compression format (closed old files are automatically compressed)

        Note: No rotation is set. Each session already creates an independent file via the {time}
        placeholder, so rotation splitting is not needed. Also, loguru's retention only executes
        when rotation triggers; if rotation=None, it executes when the sink stops instead.
        Removing rotation ensures that retention cleanup of expired files is triggered
        each time cleanup() closes the sink.
        """
        self._log_dir = Path(log_dir) if log_dir else self._get_default_log_dir()
        self._enabled = enabled
        self._log_level = log_level
        self._retention = retention
        self._compression = compression
        self._handler_id: Optional[int] = None
        self._current_log_file: Optional[Path] = None

    @staticmethod
    def _get_default_log_dir() -> Path:
        """Get the default log directory (~/.narranexus/logs/agents/)."""
        return Path.home() / ".narranexus" / "logs" / "agents"

    @property
    def current_log_file(self) -> Optional[Path]:
        """Get current log file path (template path; in the actual filename, {time} has been resolved by loguru)"""
        return self._current_log_file

    @property
    def is_active(self) -> bool:
        """Check if the logging service is in an active state"""
        return self._handler_id is not None

    def setup(self, agent_id: str, session_id: Optional[str] = None) -> Optional[Path]:
        """
        Set up file logging

        Uses loguru's {time} placeholder for filename generation, enabling retention to match
        all historical log files of the same agent via glob pattern and automatically clean up expired files.

        Filename format: {agent_id}_{time:YYYYMMDD_HHmmss}.log
        Retention match pattern: {agent_id}_.*.log(.*)

        Args:
            agent_id: Agent ID, used for generating log file names
            session_id: Optional session ID

        Returns:
            Log directory path, or None if not enabled
        """
        self.cleanup()

        if not self._enabled:
            logger.debug("File logging is disabled")
            return None

        self._log_dir.mkdir(parents=True, exist_ok=True)

        # Must use loguru's {time} placeholder, not manual datetime.strftime()
        # loguru retention replaces {time} with .* for glob matching old files
        # Manually concatenated timestamps are just fixed strings for loguru, retention cannot discover historical files
        session_suffix = f"_{session_id}" if session_id else ""
        log_template = str(
            self._log_dir / f"{agent_id}{session_suffix}_{{time:YYYYMMDD_HHmmss}}.log"
        )

        self._handler_id = logger.add(
            log_template,
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
            level=self._log_level,
            retention=self._retention,
            compression=self._compression,
            encoding="utf-8",
            enqueue=True,  # Async write, avoid blocking Agent execution
        )

        self._current_log_file = self._log_dir
        logger.info(f"📁 Log file created in: {self._log_dir} (agent={agent_id})")

        return self._current_log_file

    def cleanup(self) -> None:
        """Clean up log handlers"""
        if self._handler_id is not None:
            try:
                logger.remove(self._handler_id)
            except ValueError:
                # Handler may have already been removed
                pass
            except OSError:
                # Log file may have been deleted before compression could run
                pass
            self._handler_id = None
            self._current_log_file = None

    @contextmanager
    def session(self, agent_id: str, session_id: Optional[str] = None):
        """
        Context manager: automatic setup and cleanup of logging

        Args:
            agent_id: Agent ID
            session_id: Optional session ID

        Yields:
            logger: loguru logger instance

        Usage:
            >>> with logging_service.session("agent_123") as log:
            ...     log.info("This will be logged to file")
        """
        self.setup(agent_id, session_id)
        try:
            yield logger
        finally:
            self.cleanup()

    def __enter__(self):
        """Support with statement (requires calling setup first)"""
        return logger

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Clean up on exit"""
        self.cleanup()
        return False
