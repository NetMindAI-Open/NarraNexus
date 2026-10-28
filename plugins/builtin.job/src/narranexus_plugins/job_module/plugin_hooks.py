"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.job plugin ships.

Login, quota top-ups and provider/slot saves used to import
``job_recovery.schedule_user_no_quota_rearm`` directly; they now fire
``onDidChangeUserRunnability`` and this hook does the edge-triggered re-arm
(fire-and-forget, as before). With builtin.job disabled nothing listens.
"""
from __future__ import annotations

from narranexus.kernel.plugins.hooks import hookimpl


@hookimpl("onDidChangeUserRunnability")
def rearm_no_quota_jobs(user_id: str) -> None:
    from narranexus_plugins.job_module import job_recovery

    job_recovery.schedule_user_no_quota_rearm(user_id)


HOOKS = (rearm_no_quota_jobs,)

__all__ = ["HOOKS", "rearm_no_quota_jobs"]
