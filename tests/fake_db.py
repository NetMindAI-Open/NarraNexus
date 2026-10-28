"""
@file_name: fake_db.py
@author:
@date: 2026-09-04
@description: In-memory stand-in for AsyncDatabaseClient used by the slot /
    provider unit tests — an equality-filter store with the handful of calls
    those services make. ONE copy: it used to be pasted into three test files,
    which is how a method added to one silently went missing from the others.

    ``execute`` understands exactly one shape — ``... FROM <table> WHERE <col>
    IN (%s, ...)`` with NOTHING else in the WHERE clause — and refuses anything
    it does not model. Refusing loudly matters more than breadth: a stub that
    quietly ignored an extra ``AND slot_name = %s`` would return more rows
    than the real database and keep every test green while behaviour changed.
"""
from __future__ import annotations

import re
from collections import defaultdict


class FakeDB:
    def __init__(self):
        self.tables: dict[str, list[dict]] = defaultdict(list)

    async def get(self, table, filters=None, fields=None, **_kw):
        filters = filters or {}
        rows = [
            r for r in self.tables[table]
            if all(r.get(k) == v for k, v in filters.items())
        ]
        if fields:
            rows = [{k: r.get(k) for k in fields} for r in rows]
        return rows

    async def get_one(self, table, filters):
        rows = await self.get(table, filters)
        return rows[0] if rows else None

    async def insert(self, table, data):
        self.tables[table].append(dict(data))

    async def update(self, table, filters, data):
        rows = await self.get(table, filters)
        for r in rows:
            r.update(data)
        return len(rows)

    async def delete(self, table, filters):
        before = len(self.tables[table])
        self.tables[table] = [
            r for r in self.tables[table]
            if not all(r.get(k) == v for k, v in filters.items())
        ]
        return before - len(self.tables[table])

    _IN_ONLY = re.compile(
        r"^\s*SELECT\s+.+?\s+FROM\s+(\w+)\s+WHERE\s+(\w+)\s+IN\s*\(([%s?,\s]+)\)\s*$",
        re.IGNORECASE | re.DOTALL,
    )

    async def execute(self, query, params=None):
        m = self._IN_ONLY.match(query)
        assert m, f"FakeDB.execute models only 'SELECT … FROM t WHERE col IN (…)', got: {query!r}"
        table, col, placeholders = m.group(1), m.group(2), m.group(3)
        params = tuple(params or ())
        assert placeholders.count("%s") + placeholders.count("?") == len(params), (
            f"FakeDB.execute: placeholder/param count mismatch in {query!r}"
        )
        wanted = set(params)
        return [dict(r) for r in self.tables[table] if r.get(col) in wanted]
