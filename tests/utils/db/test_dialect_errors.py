"""
@file_name: test_dialect_errors.py
@author: Bin Liang
@date: 2026-08-19
@description: Unit tests for the shared unique-violation predicate.

Guards the single dual-dialect classifier that six insert sites now share. Uses
the real driver error messages (aiosqlite / aiomysql) so a drift in either
dialect's wording is caught here rather than in production dedup.
"""
from __future__ import annotations

from narranexus.platform.utils.db.dialect_errors import is_unique_violation


def test_sqlite_unique_message_is_a_violation():
    # aiosqlite: sqlite3.IntegrityError text
    exc = Exception("UNIQUE constraint failed: gateway_key_misuse.key_hash, gateway_key_misuse.hit_at")
    assert is_unique_violation(exc) is True


def test_mysql_duplicate_entry_message_is_a_violation():
    # aiomysql: pymysql.err.IntegrityError text
    exc = Exception("(1062, \"Duplicate entry 'abc-2026' for key 'uq_key_hash_hit_at'\")")
    assert is_unique_violation(exc) is True


def test_mysql_1062_code_alone_is_a_violation():
    # Some wrappers surface only the numeric code without the "Duplicate entry" phrase.
    exc = Exception("IntegrityError: 1062")
    assert is_unique_violation(exc) is True


def test_unrelated_error_is_not_a_violation():
    # A transient failure must NOT be swallowed as a duplicate-key hit.
    exc = Exception("(2013, 'Lost connection to MySQL server during query')")
    assert is_unique_violation(exc) is False
