"""
Tests that cloud-mode defaults in _build_from_env() match prod sizing targets.
"""
from xyz_agent_context.agent_runtime.admission import _build_from_env


def test_cloud_defaults_match_prod(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    for k in ("MAX_CONCURRENT_USERS", "MAX_LOOPS_PER_USER", "MAX_CONCURRENT_LOOPS", "MIN_FREE_MEM_MB"):
        monkeypatch.delenv(k, raising=False)
    c = _build_from_env()
    assert (c.max_users, c.max_loops_per_user, c.max_loops_global, c.min_free_mem_mb) == (20, 5, 50, 6144)


def test_env_overrides_win(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    monkeypatch.setenv("MAX_CONCURRENT_USERS", "2")
    monkeypatch.setenv("MAX_CONCURRENT_LOOPS", "10")
    c = _build_from_env()
    assert c.max_users == 2 and c.max_loops_global == 10
