"""
Custom Exceptions - Custom exception hierarchy

@file_name: exceptions.py
@author: NetMind.AI
@date: 2025-11-28
@description: Define custom exception types for the platform layer (narranexus.platform)

=============================================================================
Design Goals
=============================================================================

Solve exception swallowing problems:
- Define a clear exception hierarchy
- Preserve exception chains (cause)
- Provide rich context information
- Facilitate debugging and error tracing

Exception hierarchy:
    AgentContextError (base class)
    └── ModuleError (module-related)
        ├── DataGatheringError
        └── HookExecutionError

Usage example:
    try:
        result = await module.gather(ctx_data)
    except Exception as e:
        raise DataGatheringError(
            module="JobModule",
            message="Failed to load jobs",
            cause=e,
        ) from e

=============================================================================
"""

from __future__ import annotations

from typing import Any, Dict, Optional


# =============================================================================
# Base Exceptions
# =============================================================================

class AgentContextError(Exception):
    """
    Base exception class for the platform layer

    All custom exceptions inherit from this class, providing:
    - Unified exception interface
    - Rich context information
    - Exception chain support

    Attributes:
        message: Error message
        cause: Original exception (if any)
        context: Additional context information
    """

    def __init__(
        self,
        message: str,
        cause: Optional[Exception] = None,
        **context: Any,
    ):
        self.message = message
        self.cause = cause
        self.context = context
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        """Format the exception message"""
        parts = [self.message]

        if self.context:
            context_str = ", ".join(f"{k}={v}" for k, v in self.context.items())
            parts.append(f"[{context_str}]")

        if self.cause:
            parts.append(f"Caused by: {type(self.cause).__name__}: {self.cause}")

        return " ".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging purposes"""
        return {
            "error_type": type(self).__name__,
            "message": self.message,
            "cause": str(self.cause) if self.cause else None,
            "cause_type": type(self.cause).__name__ if self.cause else None,
            **self.context,
        }


# =============================================================================
# Module-Related Exceptions
# =============================================================================

class ModuleError(AgentContextError):
    """
    Base exception class for module-related errors

    Used for all errors related to Module operations
    """

    def __init__(
        self,
        message: str,
        module: str,
        cause: Optional[Exception] = None,
        **context: Any,
    ):
        self.module = module
        super().__init__(message, cause, module=module, **context)


class DataGatheringError(ModuleError):
    """
    Data gathering failure exception

    Raised when a Module's gather fails

    Example:
        raise DataGatheringError(
            module="JobModule",
            message="Failed to load active jobs",
            cause=original_exception,
            user_id="user_123",
        )
    """
    pass


class HookExecutionError(ModuleError):
    """
    Hook execution failure exception

    Raised when any hook of a Module fails to execute

    Example:
        raise HookExecutionError(
            module="SocialNetworkModule",
            message="after_turn failed",
            cause=original_exception,
            hook_name="after_turn",
        )
    """

    def __init__(
        self,
        message: str,
        module: str,
        hook_name: str,
        cause: Optional[Exception] = None,
        **context: Any,
    ):
        self.hook_name = hook_name
        super().__init__(message, module, cause, hook_name=hook_name, **context)


