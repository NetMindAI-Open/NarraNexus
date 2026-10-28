"""Real SIGTERM must drain a detached WS run before the actual lifespan closes DB."""

import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import pytest
from websockets.sync.client import connect


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal and inherited socket test")
def test_uvicorn_sigterm_drains_disconnected_run(tmp_path):
    env = os.environ.copy()
    env.pop("SQLITE_PROXY_URL", None)
    env.update({
        "PYTHONPATH": os.pathsep.join(sys.path),
        "DATABASE_URL": f"sqlite://{tmp_path / 'probe.db'}",
        "NARRANEXUS_PLUGIN_HOME": str(tmp_path / "plugins"),
        "NARRANEXUS_DEPLOYMENT": "local",
        "ENABLE_MANYFOLD_API": "0",
    })
    log_path = tmp_path / "uvicorn.log"
    with socket.socket() as listener, log_path.open("w") as log:
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        port = listener.getsockname()[1]
        child = subprocess.Popen(
            [sys.executable, str(Path(__file__).with_name("shutdown_probe.py")),
             str(tmp_path), str(listener.fileno())],
            env=env, stdout=log, stderr=subprocess.STDOUT,
            pass_fds=(listener.fileno(),),
        )

    def wait_for(predicate, message):
        deadline = time.monotonic() + 30  # Test harness only; no production run limit.
        while not predicate():
            assert child.poll() is None, log_path.read_text()
            assert time.monotonic() < deadline, f"{message}\n{log_path.read_text()}"
            time.sleep(0.02)

    def event_row():
        with sqlite3.connect(f"file:{tmp_path / 'probe.db'}?mode=ro", uri=True) as db:
            return db.execute(
                "SELECT state,last_event_at,finished_at,cancel_requested_at FROM events "
                "WHERE event_id='evt_sigterm_probe'"
            ).fetchone()

    try:
        wait_for(lambda: (tmp_path / "ready").exists(), "startup failed")
        with connect(
            f"ws://127.0.0.1:{port}/ws/agent/run?x_user_id=user_synthetic", proxy=None,
        ) as ws:
            ws.send(json.dumps({
                "agent_id": "agent_synthetic", "user_id": "user_synthetic",
                "input_content": "synthetic shutdown test",
            }))
            while True:
                frame = json.loads(ws.recv(timeout=10))
                assert frame["type"] != "error", frame
                if frame["type"] == "run_started":
                    break
        wait_for(lambda: (tmp_path / "ws_detached").exists(), "WS did not detach")
        assert event_row()[0] == "running"
        child.send_signal(signal.SIGTERM)
        wait_for(lambda: (tmp_path / "draining").exists(), "lifespan did not drain")
        first = event_row()
        wait_for(lambda: event_row()[1] != first[1], "heartbeat stopped during drain")
        assert child.poll() is None
        assert event_row()[0] == "running"
        assert not (tmp_path / "db_closed").exists()
        assert not (tmp_path / "housekeeping_stopped").exists()
        (tmp_path / "finish").touch()
        # Uvicorn re-raises the original SIGTERM after graceful completion.
        assert child.wait(timeout=30) in (0, -signal.SIGTERM), log_path.read_text()
        assert (tmp_path / "finalized").exists()
        assert (tmp_path / "housekeeping_stopped").exists()
        assert (tmp_path / "db_closed").exists()
        assert (tmp_path / "lifespan_done").exists()
        state, _, finished, cancellation = event_row()
        assert state == "completed" and finished and cancellation is None
        with sqlite3.connect(f"file:{tmp_path / 'probe.db'}?mode=ro", uri=True) as db:
            assert db.execute(
                "SELECT COUNT(*) FROM event_stream "
                "WHERE event_id='evt_sigterm_probe' AND kind='thinking_segment'"
            ).fetchone()[0] > 0
    finally:
        # Only this test's own synthetic child can be stopped here.
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)
