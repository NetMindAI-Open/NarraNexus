"""
@file_name: test_instructions_match_schema.py
@author: Bin Liang
@date: 2026-08-04
@description: JobModule instructions must teach ONLY job types that exist in
the JobType enum.

The instructions once taught a "recurring" type that the enum never had
(one_off / scheduled / ongoing). An instruction-following model would submit
job_type="recurring", get "Invalid job_type" back, and burn a correction
round on every scheduled-job request (live-reproduced 2026-08-04, W1). The
enum is the single source of truth; this test pins the prompt to it.
"""
import re

from xyz_agent_context.module.job_module.job_module import (
    JOB_MODULE_INSTRUCTIONS,
    JOB_MODULE_INSTRUCTIONS_STABLE,
)
from xyz_agent_context.schema.job_schema import JobType

VALID_VALUES = {t.value for t in JobType}


def _taught_type_tokens(template: str) -> set[str]:
    """Every quoted value on the `job_type` guidance line of the template."""
    tokens: set[str] = set()
    for line in template.splitlines():
        if "`job_type`" in line:
            tokens.update(re.findall(r'"([a-z_]+)"', line))
    return tokens


def test_no_phantom_recurring_type_anywhere():
    for template in (JOB_MODULE_INSTRUCTIONS, JOB_MODULE_INSTRUCTIONS_STABLE):
        assert "recurring" not in template.lower()


def test_taught_job_type_values_all_exist_in_enum():
    for template in (JOB_MODULE_INSTRUCTIONS, JOB_MODULE_INSTRUCTIONS_STABLE):
        taught = _taught_type_tokens(template)
        assert taught, "instructions no longer teach job_type values at all"
        assert taught <= VALID_VALUES, f"phantom types taught: {taught - VALID_VALUES}"


def test_every_enum_value_is_taught():
    for template in (JOB_MODULE_INSTRUCTIONS, JOB_MODULE_INSTRUCTIONS_STABLE):
        taught = _taught_type_tokens(template)
        assert taught == VALID_VALUES, f"missing from instructions: {VALID_VALUES - taught}"


def test_one_off_instructions_require_run_at():
    for template in (JOB_MODULE_INSTRUCTIONS, JOB_MODULE_INSTRUCTIONS_STABLE):
        one_off_row = next(
            line for line in template.splitlines() if line.startswith("| **ONE_OFF**")
        )
        assert "run_at (required)" in one_off_row
        assert "immediately" not in one_off_row.lower()
