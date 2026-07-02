"""Driver implementations.

Importing this package triggers ``@register`` decorators on each
concrete driver module, populating ``DRIVER_REGISTRY``. The resolver
imports the parent ``provider_driver`` package which in turn imports
this — so by the time any HTTP request comes in, the registry is full.

SystemDriver is special-cased: it self-registers only when
:func:`xyz_agent_context.utils.deployment_mode.is_cloud_mode` returns
True. Local mode never has the system pool credential loaded so a
registered SystemDriver would be a footgun.
"""
from __future__ import annotations

from xyz_agent_context.agent_framework.provider_driver.drivers import (  # noqa: F401
    custom_anthropic,
    custom_openai,
    netmind,
    yunwu,
    openrouter,
    claude_oauth,
    codex_oauth,
    system,
)
