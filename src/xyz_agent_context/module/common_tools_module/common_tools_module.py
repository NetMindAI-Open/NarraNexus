"""
@file_name: common_tools_module.py
@author: Bin Liang
@date: 2026-04-17
@description: CommonToolsModule — generic utility tools for every agent

This module covers tools that are useful to every agent regardless of domain:
- `web_search`: DuckDuckGo search (replaces Anthropic's built-in web_search for
  non-Anthropic providers like NetMind that do not ship one)

Design choices:
- module_type="capability": always loaded, no instance record, no decision LLM
- Stateless MCP tools: the tools accept plain arguments; no per-agent state
- Room to grow: extra utilities (fetch_url, read_pdf, ...) live under the same
  MCP server to keep the tool-count moderate
"""

from typing import Any, List, Optional

from loguru import logger

from xyz_agent_context.module.base import XYZBaseModule, mcp_host
from xyz_agent_context.schema import (
    ModuleConfig,
    MCPServerConfig,
    ContextData,
)
from xyz_agent_context.utils import DatabaseClient


COMMON_TOOLS_INSTRUCTIONS = """\
#### Generic Web Search

You have access to `web_search(queries: list[str], max_results_per_query: int = 5)`.
Use it whenever you need up-to-date information that is not in your context:

- Each entry in `queries` can be a **natural-language question**
  (e.g. "What is the latest iPhone 17 release date?") **or a keyword string**
  (e.g. "python asyncio gather exceptions"). Pick whichever matches how the
  information is likely written on the web.
- Pass **multiple queries at once** when you want to cover different angles
  (e.g. official docs + user discussion + recent news). They run in parallel.
- Results come back as title + URL + snippet grouped by query. If you need the
  full page, follow up with a fetch tool; do not assume the snippet is the
  whole answer.
- The search engine is DuckDuckGo — no API key required, but it is
  rate-limited, so avoid hammering it with dozens of queries in a row.

#### Reading User-Uploaded Files

When the user attaches a file (image, PDF, document, code, data — anything),
two things happen:

1. The conversation message itself carries a marker like
   `[User uploaded <kind>: name=..., path=/abs/path/..., mime=... — use Read tool to view]`.
2. If the upload happened on the *current* turn, this instruction gains a
   `## Files attached to the current message` block listing the same paths.

To act on an attachment, call the built-in `Read` tool with the absolute
path. Read is multimodal — its return shape depends on the file's type:

- **Text / code / data files** (`.md`, `.txt`, `.py`, `.ts`, `.json`,
  `.csv`, `.yaml`, ...) — returned as line-numbered text content. Every
  model can read this, regardless of vision support.
- **PDFs and rich documents** — returned as document content blocks
  (extracted text + page renders). Text extraction works on every model;
  page-level visual interpretation requires a vision-capable model.
- **Images** (`.jpg`, `.png`, `.gif`, `.webp`) — returned as a visual
  content block. Vision-capable models perceive the picture directly;
  text-only models receive opaque bytes and CANNOT see the image.

You do NOT need any custom `load_image` / `read_attachment` tool; Read
covers everything.

Rules:
- **Read each attachment AT MOST ONCE per turn.** Reading the same file
  again returns the exact same bytes — repeating it never helps and
  burns tokens.

- **Vision self-check applies ONLY to image attachments.** For text /
  code / data / document files, Read the file and proceed normally —
  there is no self-check, no model-capability concern, just answer the
  user using the file's contents. For images specifically, before you
  say a single word about what is in the picture, you MUST first write
  a short self-check listing THREE concrete, verifiable visual specifics:
    1. The dominant color of the top-left quadrant in plain words
       (e.g. "warm orange", "near-black", "pale blue").
    2. Any text visible in the image, transcribed verbatim — or
       "no text visible".
    3. A count of distinct foreground objects with brief positions
       (e.g. "2 people, both in the lower half").
  If you cannot produce any of these three with grounded specifics —
  if your candidate answer feels like a guess based on the filename or
  the conversation rather than something you literally see — then your
  underlying model does NOT support vision. The image content either
  reached you as opaque bytes (a long base64-looking string in the Read
  result) or was stripped before reaching you. This is a model
  capability limit, not a tool failure; calling Read again or scanning
  with Bash / Glob / Task will not change the outcome.
  In that case, STOP. Do NOT describe the image. Send the user a single
  message that:
    1. Confirms the file uploaded successfully.
    2. Plainly states that the current model is text-only and cannot
       read images — non-image attachments would still work.
    3. Suggests opening the agent's Settings and switching to a
       vision-capable model — any Claude family model (Opus / Sonnet /
       Haiku) supports image input.
  Do not pretend to see the picture; do not invent contents.

- If the marker says `path=<unavailable>`, the file is no longer on disk.
  Acknowledge the upload but tell the user you cannot view it; do not
  fabricate content.
- Do NOT modify or delete user-uploaded files unless the user explicitly
  asks you to.
"""


class CommonToolsModule(XYZBaseModule):
    """Always-on capability module exposing generic tools (web_search, ...)."""

    def __init__(
        self,
        agent_id: str,
        user_id: Optional[str] = None,
        database_client: Optional[DatabaseClient] = None,
        instance_id: Optional[str] = None,
        instance_ids: Optional[List[str]] = None,
    ):
        super().__init__(agent_id, user_id, database_client, instance_id, instance_ids)
        self.port = 7807
        self.instructions = COMMON_TOOLS_INSTRUCTIONS

    def get_config(self) -> ModuleConfig:
        return ModuleConfig(
            name="CommonToolsModule",
            priority=50,
            enabled=True,
            description="Generic utility tools available to every agent (web_search, ...)",
            module_type="capability",
        )

    async def hook_data_gathering(self, ctx_data: ContextData) -> ContextData:
        return ctx_data

    async def get_instructions(self, ctx_data: ContextData) -> str:
        """Return the static base instruction plus a dynamic block listing
        the absolute paths of files attached to the *current* turn.

        The dynamic block is built from `ctx_data.extra_data["attachments"]`,
        which the trigger layer (WebSocket / Lark / Job / ...) populates
        with the user's upload metadata for this run. Path resolution
        lives in `xyz_agent_context.utils.attachment_storage`.
        """
        from xyz_agent_context.utils.attachment_storage import (
            format_attachments_for_system_prompt,
        )

        attachments = []
        if ctx_data.extra_data:
            raw = ctx_data.extra_data.get("attachments")
            if isinstance(raw, list):
                attachments = raw

        if not attachments:
            return self.instructions

        appendix = format_attachments_for_system_prompt(
            attachments,
            agent_id=self.agent_id,
            user_id=self.user_id or "",
        )
        if not appendix:
            return self.instructions
        return f"{self.instructions}\n\n{appendix}"

    async def get_mcp_config(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(
            server_name="common_tools_module",
            server_url=f"http://{mcp_host()}:{self.port}/sse",
            type="sse",
        )

    def create_mcp_server(self) -> Optional[Any]:
        from xyz_agent_context.module.common_tools_module._common_tools_mcp_tools import (
            create_common_tools_mcp_server,
        )
        logger.debug(f"CommonToolsModule: creating MCP server on port {self.port}")
        return create_common_tools_mcp_server(self.port)
