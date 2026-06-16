"""
@file_name: test_task_info_human_name.py
@author: NarraNexus
@date: 2026-06-11
@description: The Job task-info prompt block must name the task creator /
execution identity by HUMAN name, not the opaque user_id (32-hex NetMind
userSystemCode in cloud mode).
"""
from __future__ import annotations


def test_template_has_no_raw_user_id_placeholder():
    from xyz_agent_context.module.job_module.prompts import JOB_TASK_INFO_TEMPLATE

    # Human-readable identity placeholders, not the raw {user_id} key.
    assert "{task_creator}" in JOB_TASK_INFO_TEMPLATE
    assert "{execution_identity}" in JOB_TASK_INFO_TEMPLATE
    assert "{user_id}" not in JOB_TASK_INFO_TEMPLATE
    assert "{execution_user_id}" not in JOB_TASK_INFO_TEMPLATE
