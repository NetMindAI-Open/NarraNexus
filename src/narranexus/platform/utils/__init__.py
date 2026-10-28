"""
Utils Package

@file_name: __init__.py
@description: Utility modules for the platform layer (narranexus.platform)

Exports:
- AsyncDatabaseClient: MySQL database operations (async driver, using aiomysql)
- DatabaseClient: Short alias for AsyncDatabaseClient
- DataLoader: Automatic batch loading utility (solves the N+1 problem)
"""

from narranexus.platform.utils.db.database import (
    AsyncDatabaseClient,
    load_db_config,
)
from narranexus.platform.utils.db.dataloader import DataLoader

# DatabaseClient is a short alias for AsyncDatabaseClient
DatabaseClient = AsyncDatabaseClient


# Text utilities
from narranexus.platform.utils.text import (
    extract_keywords,
    truncate_text,
)

# Retry utilities
from narranexus.platform.utils.retry import (
    with_retry,
    DEFAULT_RETRYABLE_EXCEPTIONS,
)

# Detached background tasks (incident lesson #2)
from narranexus.platform.utils.background_tasks import (
    spawn,
    pending as pending_background_tasks,
    drain as drain_background_tasks,
)

# Database factory (global singleton)
from narranexus.platform.utils.db.db_factory import (
    get_db_client,
    get_db_client_sync,
    close_db_client,
)

# Timezone utilities
from narranexus.platform.utils.timezone import (
    utc_now,
    to_user_timezone,
    format_for_api,
    format_for_llm,
    is_valid_timezone,
    DEFAULT_TIMEZONE,
)

# Custom exceptions
from narranexus.platform.utils.exceptions import (
    # Base
    AgentContextError,
    # Module errors
    ModuleError,
    DataGatheringError,
    HookExecutionError,
)

__all__ = [
    # Database
    "AsyncDatabaseClient",
    "DatabaseClient",
    "load_db_config",
    # DataLoader
    "DataLoader",
    # Vector calculation
    # Text utilities
    "extract_keywords",
    "truncate_text",
    # Retry
    "with_retry",
    "DEFAULT_RETRYABLE_EXCEPTIONS",
    # Background tasks
    "spawn",
    "pending_background_tasks",
    "drain_background_tasks",
    # Database factory
    "get_db_client",
    "get_db_client_sync",
    "close_db_client",
    # Timezone utilities
    "utc_now",
    "to_user_timezone",
    "format_for_api",
    "format_for_llm",
    "is_valid_timezone",
    "DEFAULT_TIMEZONE",
    # Exceptions
    "AgentContextError",
    "ModuleError",
    "DataGatheringError",
    "HookExecutionError",
]
