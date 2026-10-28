"""
@file_name: test_baseline_tables.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin the schema registry (every table, column, index) before it is split by domain.

Runs in a subprocess (see ``tests/snapshots/_subprocess.py``): ``TABLES`` is
built by whichever module packages happen to have been imported into THIS
interpreter already, so an in-process read is a function of test execution
order (and of `pytest -k` / xdist sharding), not of the code alone. A fresh
interpreter importing exactly the registration path the runtime imports is
the only way this snapshot is reproducible.
"""
from __future__ import annotations

from tests.snapshots._approval import approve
from tests.snapshots._subprocess import run_probe

_PROBE = """
import json
import narranexus.platform.module_system  # registers every builtin module's tables
from narranexus.platform.utils.db.schema_registry import TABLES

view = {}
for name, table in TABLES.items():
    view[name] = {
        "columns": [
            [c.name, c.sqlite_type, c.mysql_type, bool(c.nullable), bool(c.primary_key)]
            for c in table.columns
        ],
        "indexes": sorted(
            [idx.name, list(idx.columns), bool(getattr(idx, "unique", False))]
            for idx in getattr(table, "indexes", []) or []
        ),
    }
print(json.dumps(view))
"""


def test_schema_registry_is_unchanged():
    view = run_probe(_PROBE, env={"NARRANEXUS_DEPLOYMENT_MODE": "local"})
    approve("tables", view)
