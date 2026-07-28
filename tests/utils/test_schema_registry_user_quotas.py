"""
@file_name: test_schema_registry_user_quotas.py
@author: Bin Liang
@date: 2026-04-16
@description: Verify user_quotas table is registered in schema_registry
and has the expected shape (columns + unique index on user_id).
"""
import pytest
from xyz_agent_context.utils.db.schema_registry import get_registered_tables


def test_user_quotas_table_registered():
    tables = {t.name: t for t in get_registered_tables()}
    assert "user_quotas" in tables
    t = tables["user_quotas"]
    col_names = {c.name for c in t.columns}
    required = {
        "id", "user_id",
        "initial_input_tokens", "initial_output_tokens",
        "used_input_tokens", "used_output_tokens",
        "granted_input_tokens", "granted_output_tokens",
        "status", "created_at", "updated_at",
    }
    assert required.issubset(col_names), f"missing: {required - col_names}"


def test_user_quotas_has_unique_user_id_index():
    tables = {t.name: t for t in get_registered_tables()}
    t = tables["user_quotas"]
    unique_idx_cols = [
        idx.columns for idx in t.indexes if getattr(idx, "unique", False)
    ]
    assert ["user_id"] in unique_idx_cols
