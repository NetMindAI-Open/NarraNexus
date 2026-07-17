"""
@file_name: channel_credential_tables.py
@author: NetMind.AI
@date: 2026-07-10
@description: Single source of truth for the IM channel credential tables that
             the bundle opt-in export/import feature carries.

Why this file exists
====================
Three call sites need the SAME per-table metadata about IM channel credentials
and must not drift:

- ``builder.py``   — which tables to read when ``include_channel_credentials``
                     is on.
- ``importer.py``  — which column to force to 0 on import (the anti-double-
                     connect invariant), and to exempt these tables from the
                     generic user-attribution rewrite (their owner columns are
                     IM-namespace ids, not NarraNexus user ids).
- ``preflight``    — which columns form the bot-identity uniqueness key, to
                     detect "this bot is already bound in the target env".

Design note — activation semantics
==================================
IM credentials always import as INACTIVE (``active_col`` forced to 0). The user
must explicitly activate the channel in the new environment, which is the moment
of claiming the single WebSocket slot the IM issues per app. This prevents a
migrated agent from silently double-connecting the same bot from both the source
and target environment. See the design doc:
reference/self_notebook/specs/2026-07-10-channel-credential-export-design.md
"""

from typing import Dict, List, TypedDict


class _CredTableSpec(TypedDict):
    active_col: str          # column flipped to 0 on import (force inactive)
    identity_cols: List[str]  # bot-identity uniqueness key (clash detection)


# table_name -> spec. `agent_id` is the per-agent binding column on every table
# (registered separately in id_field_map for ID rewrite). `identity_cols` names
# the columns backed by a UNIQUE index that encodes the external bot identity;
# an empty list means the table has no bot-identity uniqueness constraint, so a
# same-bot clash cannot arise on import (agent_id is always freshly minted).
CHANNEL_CREDENTIAL_TABLES: Dict[str, _CredTableSpec] = {
    "lark_credentials": {
        "active_col": "is_active",
        # app_id is the Lark app = the real bot identity that owns the single WS
        # slot, matching the other channels' bot-identity keys. (NOT profile_name:
        # that is build_profile_name(agent_name, agent_id) — agent-derived and
        # preserved verbatim on import, so it never matches in the target env and
        # the clash check would be a silent no-op.)
        "identity_cols": ["app_id"],
    },
    "channel_slack_credentials": {
        "active_col": "enabled",
        "identity_cols": ["team_id", "bot_user_id"],
    },
    "channel_telegram_credentials": {
        "active_col": "enabled",
        "identity_cols": ["bot_user_id"],
    },
    "channel_discord_credentials": {
        "active_col": "enabled",
        "identity_cols": ["bot_user_id"],
    },
    "channel_wechat_credentials": {
        "active_col": "enabled",
        "identity_cols": [],
    },
    "channel_narramessenger_credentials": {
        "active_col": "enabled",
        "identity_cols": [],
    },
}
