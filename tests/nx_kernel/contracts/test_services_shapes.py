"""
@file_name: test_services_shapes.py
@author: Bin Liang
@date: 2026-09-07
@description: The kernel.* service contracts match the implementations they were traced from (no silent drift) — the ABC and every concrete backend, parameters AND return annotations.
"""
from __future__ import annotations

import inspect

import pytest

from narranexus.contracts import services
from narranexus.platform.utils.db import db_backend
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.db_backend_sqlite import SQLiteBackend
from narranexus.platform.utils.db.db_backend_sqlite_proxy import SQLiteProxyBackend

CONTRACT_METHODS = ("execute", "execute_write", "probe")
CONTRACT_PROPERTIES = ("dialect", "placeholder")


def _sig(cls, name):
    return inspect.signature(getattr(cls, name))


def _norm(annotation) -> str:
    """Compare annotations as text: the contract uses PEP 585 builtins, the ABC ``typing`` aliases."""
    text = annotation if isinstance(annotation, str) else getattr(annotation, "__name__", None) or str(annotation)
    return text.replace("typing.", "").replace("List", "list").replace("Dict", "dict").replace(" ", "")


def _assert_same_shape(cls, label):
    """The contract's parameters must be a prefix of the implementation's, same names, same
    defaults, same return annotation; an implementation may accept MORE (e.g.
    SQLiteBackend.execute_write's private retry knob) only with a default, so a
    contract-shaped call always binds."""
    for method in CONTRACT_METHODS:
        contract, real = _sig(services.DatabaseBackend, method), _sig(cls, method)
        c_params, r_params = list(contract.parameters), list(real.parameters)
        assert r_params[: len(c_params)] == c_params, (label, method, r_params)
        for name in c_params:
            assert contract.parameters[name].default == real.parameters[name].default, (label, method, name)
        for extra in r_params[len(c_params):]:
            assert real.parameters[extra].default is not inspect.Parameter.empty, (label, method, extra)
        assert _norm(contract.return_annotation) == _norm(real.return_annotation), (label, method, contract.return_annotation, real.return_annotation)
    for prop in CONTRACT_PROPERTIES:
        assert isinstance(getattr(cls, prop), property), (label, prop)
        assert isinstance(getattr(services.DatabaseBackend, prop), property), prop


def test_database_backend_contract_matches_the_abc_it_was_traced_from():
    _assert_same_shape(db_backend.DatabaseBackend, "abc")


# Signature parity on the CLASSES: no backend is constructed (MySQLBackend would
# need a server), so a renamed keyword or a changed return shape in any dialect
# fails here rather than at runtime in a slot-bound distribution.
@pytest.mark.parametrize("cls", [SQLiteBackend, SQLiteProxyBackend, MySQLBackend], ids=lambda c: c.__name__)
def test_every_concrete_backend_keeps_the_contract_shape(cls):
    _assert_same_shape(cls, cls.__name__)


def test_a_real_backend_satisfies_the_contract_structurally():
    assert isinstance(SQLiteBackend(":memory:"), services.DatabaseBackend)


def test_the_slot_tree_points_at_this_contract():
    from narranexus.kernel.plugins.slots import build_kernel_slot_tree

    assert build_kernel_slot_tree().get("kernel.db").contract == "narranexus.contracts.services:DatabaseBackend"
