"""
@file_name: executor_audit.py
@author: Bin Liang
@date: 2026-06-18
@description: Pydantic model for instance_executor_audit rows.

Models a single executor/loop lifecycle event stored in the
instance_executor_audit table. Used by ExecutorAuditRepository for
type-safe construction and for callers that want to work with typed
objects rather than raw dicts.

event_type values are a closed set — every known variant has a module-level
constant below. The column is VARCHAR(32) so new variants can always be added
without a migration.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel

# Known event_type values. Kept as module-level constants (same pattern as
# lark_trigger_audit_repository.py) so callers can grep for them as string
# literals rather than importing an Enum that would silently reject new values
# until the Enum is updated.
EVENT_CONTAINER_STARTED = "container_started"
EVENT_REUSED = "reused"
EVENT_CULLED = "culled"
EVENT_ORPHAN_REAPED = "orphan_reaped"
# The idle cull picked a user, then backed off because a run was live in
# ANOTHER process (executor_reaper._CullVeto). Every row is one agent run the
# pre-fix reaper would have killed mid-flight — the L3 measure of the
# cross-process guard. Only real run ids land here: "we could not tell"
# (recording switch pulled, DB unreachable) is logged, not audited, so a zero
# here does NOT mean the guard was unaffected. A rate that suddenly drops to
# zero is a reason to check the guard still runs.
EVENT_CULL_SKIPPED_BUSY = "cull_skipped_busy"
# One whole cull pass in which liveness was unreadable for EVERY candidate, so
# nothing could be culled (executor_reaper._report_pass). Distinct from
# cull_skipped_busy on purpose: mixing "we could not tell" into that metric
# would put "unknown" in its run_id column and destroy its meaning. Rows are
# per PASS, not per candidate.
#
# Only the kill-switch cause (NARRANEXUS_RUN_RECORDING_DISABLED) can reach
# this table — when the DB itself is unreadable the write needs the client
# that just failed. So this event proves blindness, it does not exhaust it;
# the reaper's repeat warning and reaper_status().blind_passes cover the rest.
EVENT_CULL_DISABLED = "cull_disabled"
EVENT_OOM_KILLED = "oom_killed"
EVENT_OOM_RETRY_OK = "oom_retry_ok"
EVENT_OOM_GAVE_UP = "oom_gave_up"
EVENT_ADMIT_QUEUED = "admit_queued"
EVENT_ADMIT_GRANTED = "admit_granted"
# Executor/broker was unreachable (container not up, broker down, or the
# :8020 connection dropped mid-run). Paired with EVENT_OOM_KILLED as the two
# executor-infra fatals the orchestration layer records + surfaces to the user.
EVENT_EXECUTOR_UNREACHABLE = "executor_unreachable"
# A verified MCP caller tried to operate an agent it does not own
# (OwnerScopedPolicy, module/identity/mcp_auth.py). Written in audit AND
# enforce mode — the audit-phase row count is the measurement that gates the
# enforce flip.
EVENT_MCP_AUTH_DENIED = "mcp_auth_denied"
# Tool-call POSTs that arrived with NO identity token, aggregated per flush
# window in audit mode (module/identity/mcp_auth.py). THE gating measurement
# for the enforce flip: zero rows means "everyone carries a token", while a
# quiet log alone could just mean nobody was looking (incident lesson #4/#5).
EVENT_MCP_AUTH_TOKENLESS = "mcp_auth_tokenless"

# Literal union of the closed set — used for type hints where strict validation
# is wanted (e.g. in tests). The repository accepts plain str so new event_types
# can be added at the call site without a code change here.
ExecutorEventType = Literal[
    "container_started",
    "reused",
    "culled",
    "orphan_reaped",
    "cull_skipped_busy",
    "cull_disabled",
    "oom_killed",
    "oom_retry_ok",
    "oom_gave_up",
    "admit_queued",
    "admit_granted",
    "executor_unreachable",
    "mcp_auth_denied",
    "mcp_auth_tokenless",
]


class ExecutorAuditEvent(BaseModel):
    """One row from the instance_executor_audit table.

    All nullable columns are Optional with default None so partial audit
    records (where not every metric is relevant) are valid.
    """

    id: Optional[int] = None
    event_type: str
    user_id: Optional[str] = None
    container_id: Optional[str] = None
    run_id: Optional[str] = None
    active_loops: Optional[int] = None
    active_users: Optional[int] = None
    queue_depth: Optional[int] = None
    free_mem_mb: Optional[int] = None
    detail_json: Optional[str] = None
    created_at: Optional[str] = None
