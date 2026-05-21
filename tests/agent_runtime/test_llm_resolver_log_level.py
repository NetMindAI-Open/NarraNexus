"""
@file_name: test_llm_resolver_log_level.py
@author: Bin Liang
@date: 2026-05-19
@description: When `get_agent_owner_llm_configs` raises
`LLMResolverError` (e.g. `SystemDefaultUnavailable` — user's free-tier
quota exhausted), `AgentRuntime.run` MUST log at WARNING level with a
single-line message and NO traceback.

Background: this is a known-business error. The traceback adds no
diagnostic value and the message itself ("system free-tier quota
exhausted. Either turn off 'Use free quota' ...") already tells the
operator exactly what to do. On EC2 jobs container 2026-05-18T16:15 →
2026-05-19T05:51 we saw 1458 ERROR-with-traceback lines for one user
(`elricwan`) whose free-tier quota was exhausted — pure noise that
buried real errors.

Per CLAUDE.md 铁律 #15 we do NOT auto-switch providers; the only
platform action is to make the log line non-noisy.
"""
from __future__ import annotations

import inspect

from xyz_agent_context.agent_runtime import agent_runtime as ar_mod


def test_llm_resolver_error_handler_does_not_use_logger_exception():
    """White-box check on the AgentRuntime.run source: the
    `except LLMResolverError` branch must not call `logger.exception`
    (which always emits ERROR + traceback). It must use a level <= WARNING.
    """
    src = inspect.getsource(ar_mod.AgentRuntime.run)
    # Locate the `except LLMResolverError` block and read up to the
    # blank line / next `except` to scope the assertion.
    marker = "except LLMResolverError"
    idx = src.find(marker)
    assert idx != -1, "Could not locate `except LLMResolverError` block"

    # Take 1500 chars of context after the `except` — wider than any
    # plausible handler body to fully cover the early-return path.
    handler_body = src[idx : idx + 1500]
    assert "logger.exception(" not in handler_body, (
        "LLMResolverError handler still calls logger.exception — this "
        "emits ERROR + traceback for a known-business error and floods "
        "logs when a user's quota is exhausted."
    )
    assert "logger.warning(" in handler_body, (
        "LLMResolverError handler must downgrade to logger.warning(...)"
    )
