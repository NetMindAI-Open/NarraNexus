"""
@file_name: team_bulletin_transfer.py
@author: NarraNexus
@date: 2026-08-10
@description: Carrying a team's bulletin through a bundle.

A bundle is how a team is handed to someone else, and the bulletin is that
team's operating conventions. A bundle that left it behind would ship a team
which has forgotten how it works — and the recipient could not tell, because a
missing bulletin looks exactly like a team that never had one.

Its own module rather than more lines inside `builder.py` / `importer.py`: both
sides of the transfer have rules that only make sense next to each other (what
is dropped on the way out is what must not be trusted on the way in), and those
two files are already long enough that the pairing would be invisible.

**Two things are deliberately dropped.**

`author_id` — every agent id and the owner's user id are re-minted on import, so
a carried id would attribute a rule to whoever happens to hold that id in the
recipient's install, or to nobody. The SOURCE survives, so an agent-written rule
still reads as one and stays reviewable; only the dangling pointer goes.

The auto-summary — it describes progress inside the exporter's install. In the
recipient's it would be a confident account of work that never happened there,
sitting in every team turn's prompt until the worker replaces it. It costs
nothing to drop: the recipient's own worker regenerates one within minutes.

**A bundle is untrusted input.** It may have been hand-edited, so the import
side re-applies the same ceilings the live surfaces enforce, and refuses to
write a summary no matter what a payload claims. Malformed entries are skipped
rather than fatal — one bad row must not abort an otherwise good import and
leave the recipient with a half-written team.
"""

from __future__ import annotations

from typing import Any, Dict, List

from loguru import logger

from narranexus.platform.repository.team_bulletin_repository import (
    TeamBulletinRepository,
)
from narranexus.platform.schema.team_schema import (
    BULLETIN_MAX_ENTRIES,
    BULLETIN_MAX_ENTRY_CHARS,
    BULLETIN_MAX_TOTAL_CHARS,
    BULLETIN_SOURCE_AGENT,
    BULLETIN_SOURCE_SUMMARY,
    BULLETIN_SOURCE_USER,
    BULLETIN_TIER_CURRENT_TASK,
    BULLETIN_TIER_LONG_TERM,
)


async def collect_bulletin_for_export(db, team_id: str) -> List[Dict[str, Any]]:
    """The team's rules in bundle shape, oldest first.

    Excludes the auto-summary and every `author_id` — see the module docstring
    for why each would be actively wrong in the recipient's install.
    """
    entries = await TeamBulletinRepository(db).list_for_team(team_id)
    return [
        {"content": e.content, "source": e.source, "tier": e.tier}
        for e in entries
        if e.source != BULLETIN_SOURCE_SUMMARY
    ]


async def write_imported_bulletin(db, team_id: str, payload: List[Dict[str, Any]]) -> int:
    """Write an imported bulletin under the NEW team id. Returns rows written.

    Enforces the live ceilings itself instead of calling `add_bulletin_entry`:
    that function raises on the first breach, which is the right behaviour for
    someone typing a rule and the wrong one here — an over-long bundle should
    yield a truncated-but-working team, not a failed import. Over-budget entries
    are dropped with a log line so the loss is visible.
    """
    if not payload:
        return 0

    repo = TeamBulletinRepository(db)
    written = 0
    total = 0
    skipped = 0

    for raw in payload:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        text = str(raw.get("content") or "").strip()
        if not text:
            skipped += 1
            continue

        # An untrusted payload must not be able to plant a permanent "progress"
        # paragraph that the recipient's worker would then treat as its slot.
        source = raw.get("source")
        source = BULLETIN_SOURCE_AGENT if source == BULLETIN_SOURCE_AGENT else BULLETIN_SOURCE_USER
        tier = BULLETIN_TIER_CURRENT_TASK if raw.get("tier") == BULLETIN_TIER_CURRENT_TASK else BULLETIN_TIER_LONG_TERM

        if len(text) > BULLETIN_MAX_ENTRY_CHARS:
            skipped += 1
            continue
        if written >= BULLETIN_MAX_ENTRIES or total + len(text) > BULLETIN_MAX_TOTAL_CHARS:
            skipped += 1
            continue

        # author_id stays NULL: the exporting ids mean nothing here.
        await repo.add(team_id=team_id, content=text, source=source, author_id=None, tier=tier)
        written += 1
        total += len(text)

    if skipped:
        logger.info(
            f"bundle_import.bulletin team={team_id} written={written} skipped={skipped} (over budget or malformed)"
        )
    return written
