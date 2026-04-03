"""
@file_name: conftest.py
@author: NexusAgent
@date: 2026-04-02
@description: Shared pytest fixtures for the test suite
"""

import pytest


@pytest.fixture
def sqlite_db_path(tmp_path):
    """Provide a temporary file path for SQLite database tests."""
    return str(tmp_path / "test.db")
