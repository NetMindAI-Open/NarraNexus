"""
@file_name: table.py
@author: Bin Liang
@date: 2026-09-03
@description: Contract for database table contributions (slot ``backend.tables``).

Pure data: a plugin describes its tables with both dialects per column and
the kernel converts them into its own ``TableDef`` at boot (so tables exist
even when the plugin never activates). Non-builtin owners must prefix their
table names with ``ext_<owner>_`` (dots in the owner become underscores);
that keeps plugin tables recognisable, prevents collisions with core tables,
and lets uninstall list what belongs to whom. Data is kept on uninstall by
default (a later explicit purge drops it).

Contract version: ``API_VERSIONS["table"]``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from narranexus.contracts._base import plugin_id_slug

EXT_PREFIX = "ext_"


def table_prefix_for(owner: str) -> str:
    """``ext_<slug>__`` — the owner's slug terminated by a DOUBLE underscore.

    The ``__`` terminator makes the prefix relation clean between DIFFERENT
    slugs (ids never contain ``__`` and never end in ``_``): ``ext_acme_weather__``
    is not a prefix of ``ext_acme_weather_x__``. Two ids with the SAME slug
    (``acme.auth-sso`` / ``acme.auth_sso``) would share a prefix, which is why
    the installer and the boot's table registration refuse a second owner of an
    already-held slug (``plugin_id_slug``).
    """
    return f"{EXT_PREFIX}{plugin_id_slug(owner)}__"


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    sqlite_type: str
    mysql_type: str
    nullable: bool = True
    default: str | None = None
    primary_key: bool = False
    auto_increment: bool = False
    unique: bool = False

    def __post_init__(self) -> None:
        if not self.sqlite_type or not self.mysql_type:
            raise ValueError(f"column {self.name!r}: both sqlite_type and mysql_type are required")


@dataclass(frozen=True)
class IndexSpec:
    name: str
    columns: tuple[str, ...]
    unique: bool = False


@dataclass(frozen=True)
class TableSpec:
    name: str
    columns: tuple[ColumnSpec, ...]
    indexes: tuple[IndexSpec, ...] = ()
    primary_key: tuple[str, ...] | None = None
    # Plugin-data schema version; the kernel records it per owner so a plugin
    # can migrate its own tables (``VERSION``/``MINOR_VERSION`` pattern).
    version: int = 1

    def __post_init__(self) -> None:
        if not self.columns:
            raise ValueError(f"table {self.name!r}: at least one column is required")
        names = [c.name for c in self.columns]
        if len(set(names)) != len(names):
            raise ValueError(f"table {self.name!r}: duplicate column names")
        known = set(names)
        for idx in self.indexes:
            missing = [c for c in idx.columns if c not in known]
            if missing:
                raise ValueError(f"table {self.name!r}: index {idx.name!r} references unknown columns {missing}")

    def check_owner(self, owner: str) -> None:
        """Raise unless a non-builtin owner's table carries its ``ext_<owner>_`` prefix."""
        if owner.startswith("builtin."):
            return
        prefix = table_prefix_for(owner)
        if not self.name.startswith(prefix):
            raise ValueError(f"table {self.name!r} of {owner!r} must be prefixed {prefix!r}")


@dataclass(frozen=True)
class MigrationSpec:
    """One ordered data migration a plugin ships for its own tables."""

    version: int
    description: str
    # symbol ``module:callable`` taking the database client; resolved by the kernel
    apply: str


__all__ = ["EXT_PREFIX", "ColumnSpec", "IndexSpec", "MigrationSpec", "TableSpec", "table_prefix_for"]
