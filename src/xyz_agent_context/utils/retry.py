"""
Retry Utility - Retry mechanism with exponential backoff

@file_name: retry.py
@author: NetMind.AI
@date: 2025-11-28
@description: Provides automatic retry capability for transient failures

=============================================================================
Design Goals
=============================================================================

Solve transient failure problems:
- Database connection drops
- Network timeouts
- External API temporarily unavailable (e.g., OpenAI)
- MCP service temporarily unresponsive

Core features:
- Exponential backoff: avoid avalanche effects
- Configurable retry count and delay
- Support for specifying retryable exception types
- Supports both synchronous and asynchronous functions
- Structured logging

Usage example:
    @with_retry(max_attempts=3, exceptions=(ConnectionError, TimeoutError))
    async def fetch_data():
        ...

=============================================================================
"""

from __future__ import annotations

import asyncio
import time
from functools import wraps
from typing import (
    Any,
    Callable,
    Optional,
    Tuple,
    Type,
    TypeVar,
    Union,
)
from loguru import logger


# =============================================================================
# Type Definitions
# =============================================================================

T = TypeVar('T')
ExceptionTypes = Union[Type[Exception], Tuple[Type[Exception], ...]]


# =============================================================================
# Default Configuration
# =============================================================================

# Default retryable exception types
DEFAULT_RETRYABLE_EXCEPTIONS: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    ConnectionResetError,
    ConnectionRefusedError,
    BrokenPipeError,
    OSError,  # Includes network-related OS errors
)

# =============================================================================
# Retry Decorator
# =============================================================================

def with_retry(
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    max_delay: float = 60.0,
    exceptions: ExceptionTypes = DEFAULT_RETRYABLE_EXCEPTIONS,
    on_retry: Optional[Callable[[Exception, int], None]] = None,
):
    """
    Retry decorator with exponential backoff

    Args:
        max_attempts: Maximum number of attempts (including the first attempt)
        delay: Initial delay in seconds
        backoff: Backoff multiplier (delay is multiplied by this value on each retry)
        max_delay: Maximum delay in seconds, prevents delays from growing too long
        exceptions: Exception types to retry on (tuple)
        on_retry: Callback function on retry, receives (exception, attempt) parameters

    Returns:
        The decorated function

    Example:
        @with_retry(max_attempts=3, exceptions=(ConnectionError,))
        async def fetch_data():
            return await api.get("/data")

        @with_retry(max_attempts=5, delay=0.5, backoff=1.5)
        def sync_operation():
            return database.query()
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        async def async_wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt < max_attempts:
                        # Calculate wait time (exponential backoff)
                        wait_time = min(delay * (backoff ** (attempt - 1)), max_delay)

                        logger.warning(
                            f"Retry {attempt}/{max_attempts} for {func.__name__}: {e}. "
                            f"Waiting {wait_time:.2f}s before next attempt."
                        )

                        # Call callback
                        if on_retry:
                            on_retry(e, attempt)

                        await asyncio.sleep(wait_time)
                    else:
                        logger.exception(
                            f"All {max_attempts} attempts failed for {func.__name__}: {e}"
                        )

            # All retries failed, raise the last exception
            raise last_exception  # type: ignore

        @wraps(func)
        def sync_wrapper(*args, **kwargs) -> T:
            last_exception: Optional[Exception] = None

            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exception = e

                    if attempt < max_attempts:
                        wait_time = min(delay * (backoff ** (attempt - 1)), max_delay)

                        logger.warning(
                            f"Retry {attempt}/{max_attempts} for {func.__name__}: {e}. "
                            f"Waiting {wait_time:.2f}s before next attempt."
                        )

                        if on_retry:
                            on_retry(e, attempt)

                        time.sleep(wait_time)
                    else:
                        logger.exception(
                            f"All {max_attempts} attempts failed for {func.__name__}: {e}"
                        )

            raise last_exception  # type: ignore

        # Return the appropriate wrapper based on function type
        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator
