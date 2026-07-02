"""
@file_name: test_executor_service_logging.py
@date: 2026-06-17
@description: Executor logs land under the (single) mounted user's
workspace dir, so each user's executor logs are isolated + persisted.
"""
from __future__ import annotations

from xyz_agent_context.agent_runtime.executor_service import _resolve_executor_log_dir


def test_single_user_subdir_logs_under_user(tmp_path):
    (tmp_path / "alice").mkdir()
    assert _resolve_executor_log_dir(str(tmp_path)) == tmp_path / "alice" / ".executor_logs"


def test_hidden_dirs_ignored(tmp_path):
    (tmp_path / "alice").mkdir()
    (tmp_path / ".cache").mkdir()
    assert _resolve_executor_log_dir(str(tmp_path)) == tmp_path / "alice" / ".executor_logs"


def test_no_subdir_falls_back_to_base(tmp_path):
    assert _resolve_executor_log_dir(str(tmp_path)) == tmp_path / ".executor_logs"


def test_ambiguous_multi_user_falls_back_to_base(tmp_path):
    (tmp_path / "alice").mkdir()
    (tmp_path / "bob").mkdir()
    assert _resolve_executor_log_dir(str(tmp_path)) == tmp_path / ".executor_logs"
