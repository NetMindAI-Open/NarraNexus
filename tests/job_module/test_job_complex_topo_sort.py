"""
@file_name: test_job_complex_topo_sort.py
@author: Bin Liang
@date: 2026-09-09
@description: B-16 — /api/jobs/complex must Kahn-sort task_keys before
creation instead of creating in raw request order.

Root cause: `create_job_complex` looked up `task_key_to_job_id[dep]` while
walking `body.jobs` in REQUEST order — a forward reference (a job whose
`depends_on` names a task_key that appears LATER in the list) raised a bare
KeyError (GitHub #285 only sanitised the message into a generic 500, it never
fixed the ordering). A cycle among task_keys hung every job in it at BLOCKED
forever with no diagnostic.
"""
from __future__ import annotations

from narranexus_plugins.job_module.routes import (
    JobComplexJobRequest,
    _topological_sort_job_complex,
)


def _job(task_key: str, depends_on=()) -> JobComplexJobRequest:
    return JobComplexJobRequest(task_key=task_key, title=task_key, depends_on=list(depends_on))


def test_already_ordered_list_is_unchanged():
    jobs = [_job("a"), _job("b", ["a"]), _job("c", ["b"])]
    ordered, error = _topological_sort_job_complex(jobs)
    assert error is None
    assert [j.task_key for j in ordered] == ["a", "b", "c"]


def test_forward_reference_is_reordered_before_its_dependent():
    """The exact GitHub #114/#109 shape: a job lists a dependency that
    appears LATER in the request body."""
    jobs = [_job("b", ["a"]), _job("a")]
    ordered, error = _topological_sort_job_complex(jobs)
    assert error is None
    keys = [j.task_key for j in ordered]
    assert keys.index("a") < keys.index("b")


def test_diamond_dependency_resolves():
    # d depends on b and c, both of which depend on a
    jobs = [_job("d", ["b", "c"]), _job("b", ["a"]), _job("c", ["a"]), _job("a")]
    ordered, error = _topological_sort_job_complex(jobs)
    assert error is None
    keys = [j.task_key for j in ordered]
    assert keys.index("a") < keys.index("b")
    assert keys.index("a") < keys.index("c")
    assert keys.index("b") < keys.index("d")
    assert keys.index("c") < keys.index("d")


def test_two_node_cycle_is_rejected():
    jobs = [_job("a", ["b"]), _job("b", ["a"])]
    ordered, error = _topological_sort_job_complex(jobs)
    assert ordered is None
    assert error is not None
    assert "a" in error and "b" in error


def test_self_dependency_is_a_cycle():
    jobs = [_job("a", ["a"])]
    ordered, error = _topological_sort_job_complex(jobs)
    assert ordered is None
    assert "a" in error


def test_independent_jobs_with_no_dependencies_all_included():
    jobs = [_job("x"), _job("y"), _job("z")]
    ordered, error = _topological_sort_job_complex(jobs)
    assert error is None
    assert {j.task_key for j in ordered} == {"x", "y", "z"}
