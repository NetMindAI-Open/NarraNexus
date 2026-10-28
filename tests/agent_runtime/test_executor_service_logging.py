"""
@file_name: test_executor_service_logging.py
@date: 2026-10-28
@description: Executor file logging goes ONLY to the dedicated log mount
(NX_EXECUTOR_LOG_DIR); when the mount is absent, stdout is the only sink.

2026-10-28 incident follow-up: logs used to land in the mounted user
workspace (``{user}/.executor_logs``), which put platform telemetry inside
the agent-readable / files-API / bundle-export surface. Deleting the
workspace fallback is the point of the change — these tests go red if it
comes back.
"""
from __future__ import annotations

from narranexus.platform.agent_runtime.executor_service import _resolve_executor_log_dir


def test_env_dir_wins(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("NX_EXECUTOR_LOG_DIR", str(tmp_path / "logs"))
    assert _resolve_executor_log_dir(str(ws)) == tmp_path / "logs"


def test_no_env_means_no_file_sink(tmp_path, monkeypatch):
    monkeypatch.delenv("NX_EXECUTOR_LOG_DIR", raising=False)
    assert _resolve_executor_log_dir(str(tmp_path)) is None


def test_workspace_fallback_is_gone(tmp_path, monkeypatch):
    """The old behavior derived {user}/.executor_logs from the workspace base;
    it must never resolve into the workspace again."""
    monkeypatch.delenv("NX_EXECUTOR_LOG_DIR", raising=False)
    (tmp_path / "alice").mkdir()
    resolved = _resolve_executor_log_dir(str(tmp_path))
    assert resolved is None


def test_env_dir_inside_workspace_is_refused(tmp_path, monkeypatch):
    """Containment guard: a misconfigured env pointing back into the mounted
    workspace (symlinks/dot-segments included) must fail closed to stdout."""
    (tmp_path / "alice").mkdir()
    monkeypatch.setenv("NX_EXECUTOR_LOG_DIR", str(tmp_path / "alice" / ".x"))
    assert _resolve_executor_log_dir(str(tmp_path)) is None
    monkeypatch.setenv("NX_EXECUTOR_LOG_DIR", str(tmp_path / "logs" / ".." / "alice"))
    assert _resolve_executor_log_dir(str(tmp_path)) is None


def _run_main(monkeypatch, tmp_path):
    import narranexus.platform.agent_runtime.executor_service as es

    added: list = []
    monkeypatch.setattr(es.logger, "add", lambda *a, **k: added.append(a))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    monkeypatch.setenv("BASE_WORKING_PATH", str(tmp_path))
    es.main()
    return added


def test_main_without_env_never_adds_a_file_sink(tmp_path, monkeypatch):
    """main()-level guard: reintroducing any fallback (e.g. `or base/.executor_logs`)
    at the call site turns this red even if the resolver stays clean."""
    monkeypatch.delenv("NX_EXECUTOR_LOG_DIR", raising=False)
    (tmp_path / "alice").mkdir()
    added = _run_main(monkeypatch, tmp_path)
    assert added == []
    assert not list(tmp_path.rglob(".executor_logs"))


def test_main_with_env_sinks_into_the_dedicated_dir(tmp_path, monkeypatch):
    log_root = tmp_path / "exec-logs"
    monkeypatch.setenv("NX_EXECUTOR_LOG_DIR", str(log_root))
    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setenv("BASE_WORKING_PATH", str(ws))
    import narranexus.platform.agent_runtime.executor_service as es
    added: list = []
    monkeypatch.setattr(es.logger, "add", lambda *a, **k: added.append(a))
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: None)
    es.main()
    assert len(added) == 1 and str(log_root) in added[0][0]
    assert not list(ws.rglob(".executor_logs"))


def test_env_dir_symlinking_into_workspace_is_refused(tmp_path, monkeypatch):
    """resolve() must see through a symlink that points the 'dedicated' dir
    back into the workspace."""
    ws = tmp_path / "ws"
    (ws / "alice").mkdir(parents=True)
    link = tmp_path / "innocent-logs"
    link.symlink_to(ws / "alice")
    monkeypatch.setenv("NX_EXECUTOR_LOG_DIR", str(link))
    assert _resolve_executor_log_dir(str(ws)) is None
