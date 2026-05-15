"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle package — Export & Import .nxbundle files

Subproject 2: Bundle Export/Import.

Public API:
- builder.build_bundle:  serialize the closure of selected agents into a .nxbundle zip
- importer.preflight:    parse + validate + diff against the destination instance
- importer.confirm:      execute the import (ID rewrite + name suffix + DB write)
"""

from .id_schema import ID_KINDS, build_all_id_regex
from .id_field_map import STRUCTURED_ID_FIELDS, ID_KIND_PREFIXES, gen_new_id

__all__ = [
    "ID_KINDS",
    "build_all_id_regex",
    "STRUCTURED_ID_FIELDS",
    "ID_KIND_PREFIXES",
    "gen_new_id",
]
