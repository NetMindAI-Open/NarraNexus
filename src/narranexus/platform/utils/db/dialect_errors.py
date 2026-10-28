"""
@file_name: dialect_errors.py
@author: Bin Liang
@date: 2026-08-19
@description: Cross-dialect DB error classification helpers.

The unique-key-violation test had been hand-copied into six insert sites (the
seen-message dedup repositories, the product-analytics writer, the instance-link
race guard, the gateway-key-misuse idempotency guard, and the bundle importer),
and the copies had drifted (some missed the MySQL ``1062`` error code, one matched
on the over-broad bare substrings ``unique`` / ``duplicate``). A unique-violation
looks the same regardless of which table raised it, so the classifier lives here —
next to the other cross-dialect DB helper (``dialect_time``) — rather than inside
any one repository, which would force the bundle importer to reach across into a
repository just to import a predicate.
"""
from __future__ import annotations


def is_unique_violation(exc: BaseException) -> bool:
    """SQLite/MySQL dual-dialect unique-key violation test (no driver imports).

    Matches on the error text so neither ``aiosqlite`` nor ``aiomysql`` has to be
    imported here: aiosqlite raises ``"UNIQUE constraint failed: ..."`` and
    aiomysql raises ``"(1062, \"Duplicate entry '...' for key '...'\")"``. Kept
    deliberately specific (the full phrases, not bare ``unique`` / ``duplicate``)
    so an unrelated error whose text merely mentions those words is not
    mis-classified as a duplicate-key hit.
    """
    msg = str(exc).lower()
    return (
        "unique constraint failed" in msg  # sqlite
        or "duplicate entry" in msg        # mysql
        or "1062" in msg                   # mysql err code
    )
