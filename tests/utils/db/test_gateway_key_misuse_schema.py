"""
@file_name: test_gateway_key_misuse_schema.py
@author: Bin Liang
@date: 2026-08-19
@description: gateway_key_misuse is registered with both dialects filled and the
attribution/index shape the security monitor reader needs.

gateway_key_misuse is the authoritative record of abnormal / unauthorized use of
a gateway key: single writer is the backend admin gateway-key-misuse endpoint;
the security monitor reads it read-only. user_id is the authoritative
reverse-resolved id (nullable: an unresolved event is an alert-only row, never
actioned). Verified here at both the TableDef level and by generating the
SQLite + MySQL DDL (dual-dialect contract) so a missing type or a bad default is
caught before startup.
"""
from narranexus.platform.utils.db.schema_registry import (
    TABLES,
    generate_mysql_ddl,
    generate_sqlite_ddl,
    get_registered_tables,
)


def test_gateway_key_misuse_registered_dual_dialect():
    t = TABLES["gateway_key_misuse"]
    cols = {c.name: c for c in t.columns}
    # authoritative attribution column + monitor watermark PK
    assert cols["id"].primary_key and cols["id"].auto_increment
    # user_id is VARCHAR(64) — the codebase-wide id width (users.user_id is
    # VARCHAR(64) UNIQUE); a wider column would let a 65..128-char value be
    # stored as an authoritative attribution the ladder could not resolve back.
    assert cols["user_id"].sqlite_type == "TEXT" and cols["user_id"].mysql_type == "VARCHAR(64)"
    assert cols["disposition_status"].default == "'pending'"
    for c in t.columns:  # dual-dialect contract: both types always filled
        assert c.sqlite_type, f"{c.name} missing sqlite_type"
        assert c.mysql_type, f"{c.name} missing mysql_type"
    idx = {i.name for i in t.indexes}
    assert "idx_gateway_key_misuse_user" in idx
    assert "idx_gateway_key_misuse_status" in idx


def test_gateway_key_misuse_has_unique_dedup_index():
    # (key_hash, hit_at) is UNIQUE so a caller's at-least-once retry collapses to
    # one row instead of a duplicate the response ladder would act on twice.
    t = TABLES["gateway_key_misuse"]
    dedup = next((i for i in t.indexes if i.name == "idx_gateway_key_misuse_dedup"), None)
    assert dedup is not None, "missing (key_hash, hit_at) dedup index"
    assert dedup.unique is True
    assert dedup.columns == ["key_hash", "hit_at"]


def test_gateway_key_misuse_user_id_is_nullable():
    # An unresolved event is recorded as an alert-only row with user_id=NULL;
    # the monitor never actions a NULL id. So the column MUST be nullable.
    col = next(c for c in TABLES["gateway_key_misuse"].columns if c.name == "user_id")
    assert col.nullable is not False


def test_gateway_key_misuse_reader_columns_present():
    # The monitor-side reader selects exactly this column set — pin it so a
    # rename here can't silently break the reader.
    col_names = {c.name for c in TABLES["gateway_key_misuse"].columns}
    required = {"id", "user_id", "run_id", "key_hash", "caller_ip", "caller_ua",
                "model", "hit_at"}
    assert required.issubset(col_names), f"missing: {required - col_names}"


def test_gateway_key_misuse_registered_via_public_accessor():
    tables = {t.name: t for t in get_registered_tables()}
    assert "gateway_key_misuse" in tables


def test_gateway_key_misuse_sqlite_ddl_generates():
    stmts = generate_sqlite_ddl(TABLES["gateway_key_misuse"])
    create = stmts[0]
    assert "CREATE TABLE IF NOT EXISTS gateway_key_misuse" in create
    # auto-increment PK renders as SQLite's INTEGER PRIMARY KEY AUTOINCREMENT
    assert "id INTEGER PRIMARY KEY AUTOINCREMENT" in create
    # SQLite keeps the native default expression
    assert "DEFAULT (datetime('now'))" in create
    assert "DEFAULT 'pending'" in create
    idx_sql = " ".join(stmts[1:])
    assert "idx_gateway_key_misuse_user" in idx_sql
    assert "idx_gateway_key_misuse_status" in idx_sql


def test_gateway_key_misuse_mysql_ddl_generates():
    stmts = generate_mysql_ddl(TABLES["gateway_key_misuse"])
    create = stmts[0]
    assert "CREATE TABLE IF NOT EXISTS `gateway_key_misuse`" in create
    assert "`id` BIGINT UNSIGNED NOT NULL AUTO_INCREMENT" in create
    assert "PRIMARY KEY (`id`)" in create
    # DATETIME(6) is NOT a LOB, so the timestamp default is translated to MySQL
    assert "CURRENT_TIMESTAMP(6)" in create
    # VARCHAR(32) status keeps its literal default
    assert "DEFAULT 'pending'" in create
    idx_sql = " ".join(stmts[1:])
    assert "`idx_gateway_key_misuse_user`" in idx_sql
    assert "`idx_gateway_key_misuse_status`" in idx_sql
