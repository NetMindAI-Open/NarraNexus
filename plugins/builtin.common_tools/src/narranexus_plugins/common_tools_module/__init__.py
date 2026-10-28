"""CommonToolsModule package — generic utility tools available to every agent.

Exposes `web_search` (DuckDuckGo) and leaves room for future utilities like
`fetch_url`, `read_pdf`, etc. Capability-typed so it is always loaded without
an LLM judgement.
"""

from narranexus_plugins.common_tools_module.common_tools_module import CommonToolsModule

__all__ = ["CommonToolsModule"]
