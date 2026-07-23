"""
@file_name: test_schema_registry_billing_audit.py
@author: Bin Liang
@date: 2026-07-22
@description: Verify the billing-audit schema additions are registered:
cost_records gains user_id / provider_source (+ index), and the new
quota_deductions ledger table exists with its self-audit columns.
"""
from xyz_agent_context.utils.schema_registry import get_registered_tables


def test_cost_records_has_user_id_and_provider_source():
    tables = {t.name: t for t in get_registered_tables()}
    assert "cost_records" in tables
    col_names = {c.name for c in tables["cost_records"].columns}
    assert {"user_id", "provider_source"}.issubset(col_names), (
        f"missing: {{'user_id', 'provider_source'}} - {col_names}"
    )


def test_cost_records_user_id_is_nullable():
    tables = {t.name: t for t in get_registered_tables()}
    col = next(c for c in tables["cost_records"].columns if c.name == "user_id")
    # Nullable so background / non-user calls (no auth context) don't fail.
    assert col.nullable is not False


def test_cost_records_has_user_id_index():
    tables = {t.name: t for t in get_registered_tables()}
    idx_cols = [idx.columns for idx in tables["cost_records"].indexes]
    assert ["user_id"] in idx_cols


def test_quota_deductions_table_registered():
    tables = {t.name: t for t in get_registered_tables()}
    assert "quota_deductions" in tables
    col_names = {c.name for c in tables["quota_deductions"].columns}
    required = {
        "id", "user_id", "input_tokens", "output_tokens",
        "cost_record_id", "provider_source", "model", "agent_id", "created_at",
    }
    assert required.issubset(col_names), f"missing: {required - col_names}"


def test_quota_deductions_has_user_and_created_indexes():
    tables = {t.name: t for t in get_registered_tables()}
    idx_cols = [idx.columns for idx in tables["quota_deductions"].indexes]
    assert ["user_id"] in idx_cols
    assert ["created_at"] in idx_cols


def test_quota_deductions_user_id_not_null():
    tables = {t.name: t for t in get_registered_tables()}
    col = next(
        c for c in tables["quota_deductions"].columns if c.name == "user_id"
    )
    assert col.nullable is False


def test_quota_deductions_columns_have_both_dialects():
    """auto_migrate silently skips a column missing either dialect type."""
    tables = {t.name: t for t in get_registered_tables()}
    for c in tables["quota_deductions"].columns:
        assert c.sqlite_type, f"{c.name} missing sqlite_type"
        assert c.mysql_type, f"{c.name} missing mysql_type"
