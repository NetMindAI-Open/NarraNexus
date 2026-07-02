#!/usr/bin/env python3
"""
@file_name: seed_narramessenger_credential.py
@date: 2026-06-17
@description: Seed one channel_narramessenger_credentials row for testing.

The NarraMessenger bind UI (Block 6) does not exist yet, but the trigger reads
active credentials from the DB. This CLI inserts/updates one row so a known
agent can be connected and the gateway-poll loop tested end-to-end.

Usage:
    uv run python scripts/seed_narramessenger_credential.py \
        --agent-id agent_baefc6149d7f \
        --bearer-token-file "$HOME/.nexusagent/workspaces/agent_baefc6149d7f_hongyi test 2/.narra/agent-runtime-token" \
        --backend-base-url https://api.netmind.chat \
        --matrix-user-id @agent-e7726996:matrix.netmind.chat \
        --matrix-homeserver-url https://matrix.netmind.chat \
        --nexus-principal-id c1f7267b-abd9-4512-8e4b-2119e52e7c09

The bearer token may be passed inline (--bearer-token) or read from a file
(--bearer-token-file); the file form keeps the secret out of shell history.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed a NarraMessenger credential row.")
    p.add_argument("--agent-id", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--bearer-token", help="Runtime bearer token (inline).")
    g.add_argument("--bearer-token-file", help="Path to a file containing the bearer token.")
    p.add_argument("--backend-base-url", required=True, help="e.g. https://api.netmind.chat")
    p.add_argument("--matrix-user-id", required=True, help="e.g. @agent-xxx:matrix.netmind.chat")
    p.add_argument("--matrix-homeserver-url", default="", help="e.g. https://matrix.netmind.chat")
    p.add_argument("--nexus-principal-id", default="")
    p.add_argument("--nexus-profile-id", default="")
    p.add_argument("--bind-room-id", default="")
    p.add_argument("--owner-matrix-user-id", default="")
    p.add_argument("--owner-name", default="")
    p.add_argument("--disabled", action="store_true", help="Insert with enabled=0.")
    return p.parse_args()


async def _run(args: argparse.Namespace) -> int:
    # Imported lazily so the module's settings/env load only when invoked.
    import xyz_agent_context.settings  # noqa: F401
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.schema_registry import auto_migrate
    from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
        NarramessengerCredential,
        NarramessengerCredentialManager,
    )

    if args.bearer_token_file:
        token = Path(args.bearer_token_file).expanduser().read_text().strip()
    else:
        token = (args.bearer_token or "").strip()
    if not token:
        print("ERROR: bearer token is empty", file=sys.stderr)
        return 2

    db = await get_db_client()
    await auto_migrate(db._backend)

    cred = NarramessengerCredential(
        agent_id=args.agent_id,
        bearer_token=token,
        backend_base_url=args.backend_base_url,
        matrix_homeserver_url=args.matrix_homeserver_url,
        matrix_user_id=args.matrix_user_id,
        nexus_principal_id=args.nexus_principal_id,
        nexus_profile_id=args.nexus_profile_id,
        bind_room_id=args.bind_room_id,
        owner_matrix_user_id=args.owner_matrix_user_id,
        owner_name=args.owner_name,
        connection_mode="gateway",
        enabled=not args.disabled,
    )
    mgr = NarramessengerCredentialManager(db)
    await mgr.upsert(cred)
    print(
        f"Seeded channel_narramessenger_credentials for agent_id={args.agent_id} "
        f"(matrix_user_id={args.matrix_user_id}, enabled={not args.disabled})"
    )
    return 0


def main() -> None:
    args = _parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
