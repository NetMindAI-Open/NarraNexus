"""
@file_name: _narra_guide.py
@date: 2026-07-20
@description: Curated command reference for the ``narra_guide`` MCP tool.

We deliberately do NOT serve narra's live ``runtime.md``. That document is written
for a runtime that installs / configures / runs narra-cli itself (npm install,
``configure --endpoint``, ``.narra/agent-runtime-token``, ``chmod`` of a config
dir, …). In OUR architecture narra-cli is platform-provided via the ``narra_cli``
MCP tool, so those setup instructions are actively harmful: a capable agent that
follows the guide tries to install + ``configure`` narra-cli in its sandbox and
fails (2026-07-20 dev incident — Opus hit "narra-cli cannot init its config dir,
chmod permission denied").

Instead we serve a small **curated command reference** (``resources/narra-runtime.md``)
that carries a strong "platform provides it, use the tool" banner and only the
command *shapes*. We do NOT lose freshness: the agent gets the exact / latest
flags of any command from the live CLI via ``narra_cli("<domain> --help")``. The
only maintenance this needs is when narra adds a whole new top-level DOMAIN —
which already requires a ``ALLOWED_DOMAINS`` whitelist edit in
``_narra_command_security``, so the two move together.

Independent per binding rule #3.
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

_CURATED_PATH = Path(__file__).parent / "resources" / "narra-runtime.md"

# Minimal built-in fallback if the resource file is somehow missing (e.g. a
# non-editable wheel that dropped package data). Keeps the "use the tool, don't
# set up narra-cli" invariant even in that degraded case.
_BUILTIN = (
    "# narra-cli (via the narra_cli MCP tool)\n\n"
    "narra-cli is provided by the platform — do NOT install / configure it or "
    "pass a token. Run commands only via `narra_cli(command=\"...\")`; use "
    "`narra_cli(command=\"<domain> --help\")` for exact flags. Domains: room, im, "
    "speech, explore, status. Reply with `narra_reply`; send chat with "
    "`narra_send` / `narra_send_media`.\n"
)


def get_guide() -> str:
    """Return the curated narra-cli command reference (static, platform-adapted)."""
    try:
        return _CURATED_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"[narra_guide] curated reference unreadable ({_CURATED_PATH}): {e}")
        return _BUILTIN
