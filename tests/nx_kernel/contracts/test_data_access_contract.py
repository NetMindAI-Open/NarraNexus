"""
@file_name: test_data_access_contract.py
@author: Bin Liang
@date: 2026-09-04
@description: DataAccessSpec names an AgentDataStore method and carries its async handler.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import API_VERSIONS
from narranexus.contracts.data_access import DataAccessSpec


async def _h(db, *a):
    return {"success": True}


def test_kind_is_versioned_and_spec_validates():
    assert API_VERSIONS["data_access"] == 0
    spec = DataAccessSpec("job_create", _h)
    assert spec.name == "job_create" and spec.handler is _h
    with pytest.raises(ValueError):
        DataAccessSpec("not a name", _h)
