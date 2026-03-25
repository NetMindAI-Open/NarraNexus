"""
Utils Package

@file_name: __init__.py
@description: Utility modules for xyz_agent_context

Exports:
- AsyncDatabaseClient: MySQL database operations (async driver, using aiomysql)
- DatabaseClient: Short alias for AsyncDatabaseClient
- DataLoader: Automatic batch loading utility (solves the N+1 problem)
- EmbeddingClient: Text embedding generation (OpenAI)
- Convenience functions: get_embedding, load_db_config
"""

from xyz_agent_context.utils.database import (
    AsyncDatabaseClient,
    load_db_config,
)
from xyz_agent_context.utils.dataloader import DataLoader

# DatabaseClient is a short alias for AsyncDatabaseClient
DatabaseClient = AsyncDatabaseClient

# Embedding utilities (moved to agent_framework/llm_api/)
from xyz_agent_context.agent_framework.llm_api.embedding import (
    EmbeddingClient,
    get_embedding,
    prepare_job_text_for_embedding,
    # Vector calculation utilities
    cosine_similarity,
    compute_average_embedding,
)

# Text utilities
from xyz_agent_context.utils.text import (
    extract_keywords,
    truncate_text,
)

# Retry utilities
from xyz_agent_context.utils.retry import (
    with_retry,
    DEFAULT_RETRYABLE_EXCEPTIONS,
)

# Database factory (global singleton)
from xyz_agent_context.utils.db_factory import (
    get_db_client,
    get_db_client_sync,
    close_db_client,
)

# Timezone utilities
from xyz_agent_context.utils.timezone import (
    utc_now,
    to_user_timezone,
    format_for_api,
    format_for_llm,
    is_valid_timezone,
    DEFAULT_TIMEZONE,
)

# Custom exceptions
from xyz_agent_context.utils.exceptions import (
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
    # Embeddings
    "EmbeddingClient",
    "get_embedding",
    "prepare_job_text_for_embedding",
    # Vector calculation
    "cosine_similarity",
    "compute_average_embedding",
    # Text utilities
    "extract_keywords",
    "truncate_text",
    # Retry
    "with_retry",
    "DEFAULT_RETRYABLE_EXCEPTIONS",
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
