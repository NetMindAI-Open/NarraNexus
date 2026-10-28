"""
@file_name: _narra_guide.py
@date: 2026-07-20
@description: Curated command reference for the ``narra_guide`` MCP tool.

We deliberately do NOT serve narra's live ``runtime.md``. That document is written
for a runtime that installs / configures / runs narra-cli itself (``npx
@narra-im/narra-cli@latest ... --endpoint $NARRA_API_ENDPOINT --token-file
.narra/<id>/agent-runtime-token``, ``AGENTS.md`` bootstrap blocks, …). In OUR
architecture narra-cli is platform-provided via the ``narra_cli`` MCP tool, which
injects the token AND the binding's endpoint per call, so those setup
instructions are actively harmful: a capable agent that follows the guide tries
to install narra-cli in its sandbox and fails (2026-07-20 dev incident — Opus hit
"narra-cli cannot init its config dir, chmod permission denied"), or keeps its
own token file and "compares" tokens in chat (2026-09-09 prod incident).

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
    "pass `--token` / `--token-file` / `--endpoint` (token and endpoint are "
    "injected from your binding; ignore the `npx ...` USAGE line `--help` "
    "prints). Run commands only via `narra_cli(command=\"...\")`; use "
    "`narra_cli(command=\"<domain> --help\")` for exact flags. Domains: room, im, "
    "speech, explore, status. Reply with `narra_reply`; send chat with "
    "`narra_send` / `narra_send_media`.\n\n"
    "When a call fails: give the user the error code and what it might mean; "
    "never paste a token or credential file into a message. For "
    "`agent-token-invalid` / `no_endpoint` / an unexpected auth error the "
    "platform injected the credential, so do not assert a cause — file it "
    "with `submit_feedback(category=\"error\", "
    "dedup_key=\"narra_cli:<code>\", ...)`; the dedup_key keeps a "
    "platform-wide outage to one report per agent per code. Say the team was "
    "notified only if that call's result says so. "
    "`official-agent-required` and `no_credential` are by-design answers, not "
    "defects.\n"
)


def get_guide() -> str:
    """Return the curated narra-cli command reference (static, platform-adapted)."""
    try:
        return _CURATED_PATH.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"[narra_guide] curated reference unreadable ({_CURATED_PATH}): {e}")
        return _BUILTIN
