"""AwarenessModule package surface.

Re-exports the shared, dialect-safe write helper (and the identity-note string
helpers it is built from) from ``_awareness_writes`` so the AgentDataStore seam's
DirectStore, the agent-scoped backend twin route, and tests import the PACKAGE
rather than reaching the private leaf. The AwarenessModule class is deliberately
NOT re-exported here: module_registry already imports it from ``.awareness_module``,
and re-exporting it would introduce an initialization-order dependency on that
submodule for anyone importing just the write helper. Same choice job_module
makes. (This is not about avoiding module-load cost — importing any submodule of
``narranexus.platform.module_system`` already runs the parent package's module_registry.)"""
from ._awareness_writes import (
    IDENTITY_CHANGE_SECTION,
    MAX_IDENTITY_CHANGE_ENTRIES,
    build_identity_change_note,
    carry_over_platform_record,
    record_identity_change,
    retire_self_name,
    reconcile_identity_record,
    build_identity_reconciliation_note,
    declared_self_name,
    identity_note_asserts,
    merge_identity_change_note,
    update_agent_profile_from_args,
)

__all__ = [
    "IDENTITY_CHANGE_SECTION",
    "MAX_IDENTITY_CHANGE_ENTRIES",
    "build_identity_change_note",
    "carry_over_platform_record",
    "record_identity_change",
    "retire_self_name",
    "reconcile_identity_record",
    "build_identity_reconciliation_note",
    "declared_self_name",
    "identity_note_asserts",
    "merge_identity_change_note",
    "update_agent_profile_from_args",
]
