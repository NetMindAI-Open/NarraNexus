"""
@file_name: test_install.py
@author:
@date: 2026-09-22
@description: Tests for the browser runtime install coordinator.

The runtime is user-installed from the UI (Owner decision 2026-09-22), so this
coordinator is what stands between "user clicked install" and a download. The
cases that actually bite in production are concurrency and idempotency: two
tabs, or a tab plus an agent turn, both asking to install at the same moment
must not produce two downloads of the same 150 MB.

The downloader is injected; no network here.
"""
from __future__ import annotations

from pathlib import Path
import asyncio

import pytest

from narranexus.platform.browser._browser_impl.install import (
    InstallCoordinator,
    InstallOutcome,
    InstallProgress,
)

EXE = Path("/tmp/nx-browser/chrome")


class FakeDownloader:
    """Records calls; completion is driven by the test, not by time."""

    def __init__(self, result: InstallOutcome | None = None, raises: Exception | None = None):
        self.calls = 0
        self._result = result or InstallOutcome(ok=True, executable=EXE, error=None)
        self._raises = raises
        self.cancelled = False

    async def __call__(self, *, on_progress, cancelled) -> InstallOutcome:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        on_progress(InstallProgress(phase="downloading", bytes_done=1, bytes_total=10))
        if cancelled():
            self.cancelled = True
            return InstallOutcome(ok=False, executable=None, error="cancelled")
        return self._result


# ── progress model ───────────────────────────────────────────────────────────


def test_progress_percent_is_none_when_total_unknown():
    """Some CDNs answer without Content-Length. A progress bar that invents a
    percentage from an unknown total lies to the user; None renders as
    indeterminate instead."""
    p = InstallProgress(phase="downloading", bytes_done=5, bytes_total=None)
    assert p.percent is None


def test_progress_percent_rounds_down_and_clamps():
    assert InstallProgress(phase="downloading", bytes_done=5, bytes_total=10).percent == 50
    assert InstallProgress(phase="downloading", bytes_done=99, bytes_total=10).percent == 100
    assert InstallProgress(phase="downloading", bytes_done=0, bytes_total=10).percent == 0


def test_progress_percent_survives_zero_total():
    assert InstallProgress(phase="extracting", bytes_done=0, bytes_total=0).percent is None


# ── idempotency ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_install_when_already_ready_does_not_download():
    """Design §8.3: install is idempotent. Clicking it on a working runtime is
    a no-op, not another 150 MB."""
    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: True)

    out = await c.install()

    assert out.ok is True
    assert dl.calls == 0


@pytest.mark.asyncio
async def test_install_when_absent_downloads_once():
    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False)

    out = await c.install()

    assert out.ok is True
    assert out.executable == EXE
    assert dl.calls == 1


# ── concurrency ──────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_installs_share_one_download():
    """Two tabs (or a tab + an agent turn) must not each start a download."""
    import asyncio

    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False)

    outs = await asyncio.gather(c.install(), c.install(), c.install())

    assert dl.calls == 1
    assert all(o.ok for o in outs)


@pytest.mark.asyncio
async def test_installing_flag_is_true_only_while_in_flight():
    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False)

    assert c.installing is False
    await c.install()
    assert c.installing is False


# ── failure and cancel ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_downloader_exception_becomes_a_failed_outcome():
    """A dead CDN must not take down the caller (an agent turn, a render
    request); it becomes a reportable outcome with the reason kept."""
    dl = FakeDownloader(raises=RuntimeError("connection reset"))
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False)

    out = await c.install()

    assert out.ok is False
    assert "connection reset" in (out.error or "")
    assert c.installing is False


@pytest.mark.asyncio
async def test_failure_does_not_wedge_the_coordinator():
    """After a failed attempt the user must be able to press install again."""
    dl_bad = FakeDownloader(raises=RuntimeError("boom"))
    c = InstallCoordinator(downloader=dl_bad, is_ready=lambda: False)
    await c.install()

    dl_ok = FakeDownloader()
    c.downloader = dl_ok
    out = await c.install()

    assert out.ok is True
    assert dl_ok.calls == 1


@pytest.mark.asyncio
async def test_cancel_is_observed_by_the_downloader():
    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False)
    task = asyncio.create_task(c.install())
    await asyncio.sleep(0)
    c.cancel()
    out = await task

    assert dl.cancelled is True
    assert out.ok is False


@pytest.mark.asyncio
async def test_cancel_resets_so_a_later_install_can_run():
    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False)
    task = asyncio.create_task(c.install())
    await asyncio.sleep(0)
    c.cancel()
    await task

    out = await c.install()

    assert out.ok is True


# ── progress reporting ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_progress_is_exposed_while_running_and_cleared_after():
    seen: list[InstallProgress] = []
    dl = FakeDownloader()
    c = InstallCoordinator(downloader=dl, is_ready=lambda: False, on_progress=seen.append)

    await c.install()

    assert [p.phase for p in seen] == ["downloading"]
    assert c.progress is None


@pytest.mark.asyncio
async def test_a_throwing_progress_listener_does_not_fail_the_install():
    """A UI subscriber that blew up must not cost the user their download."""

    def bad(_p: InstallProgress) -> None:
        raise ValueError("listener bug")

    c = InstallCoordinator(downloader=FakeDownloader(), is_ready=lambda: False, on_progress=bad)

    out = await c.install()

    assert out.ok is True


@pytest.mark.asyncio
async def test_disconnected_waiter_does_not_reset_running_cancel_or_progress():
    started, finish = asyncio.Event(), asyncio.Event()

    async def download(*, on_progress, cancelled):
        on_progress(InstallProgress("downloading", 5, 10))
        started.set()
        await finish.wait()
        return InstallOutcome(not cancelled(), None, "cancelled" if cancelled() else None)

    coordinator = InstallCoordinator(downloader=download, is_ready=lambda: False)
    waiter = asyncio.create_task(coordinator.install())
    await started.wait()
    coordinator.cancel()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert coordinator.progress is not None
    finish.set()
    assert (await coordinator.install()).error == "cancelled"
    assert coordinator.progress is None


@pytest.mark.asyncio
async def test_readiness_exception_is_reported_as_an_outcome():
    def broken():
        raise OSError("permission denied")

    result = await InstallCoordinator(downloader=FakeDownloader(), is_ready=broken).install()
    assert not result.ok
    assert "permission denied" in result.error


@pytest.mark.asyncio
async def test_cancel_while_idle_does_not_cancel_the_next_install():
    coordinator = InstallCoordinator(downloader=FakeDownloader(), is_ready=lambda: False)
    coordinator.cancel()
    assert (await coordinator.install()).ok


def test_manual_install_command_preserves_paths_and_process_configuration(tmp_path, monkeypatch):
    import shlex
    import sys
    from narranexus.platform.browser._browser_impl.install import manual_install_help

    root = tmp_path / "user's browser; $(touch unwanted)"
    monkeypatch.setenv("NARRANEXUS_BROWSER_HOME", str(root))
    monkeypatch.setenv("NARRANEXUS_BROWSER_DOWNLOAD_HOST", "https://mirror.example/cft")
    monkeypatch.setenv("NARRANEXUS_BROWSER_MANIFEST_URL", "https://mirror.example/index.json")
    help_info = manual_install_help()
    argv = shlex.split(help_info["command"])
    assert argv == help_info["argv"]
    assert argv[:4] == [sys.executable, "-m", "narranexus.platform.browser", "install"]
    assert argv[argv.index("--root") + 1] == str(root)
    assert argv[argv.index("--download-host") + 1] == "https://mirror.example/cft"
    assert argv[argv.index("--manifest-url") + 1] == "https://mirror.example/index.json"
    assert not root.exists()


def test_manual_install_help_quotes_powershell_arguments(tmp_path, monkeypatch):
    import sys
    from narranexus.platform.browser._browser_impl.install import manual_install_help

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "executable", "C:\\Users\\O'Brien\\App Data\\python.exe")
    info = manual_install_help(root=tmp_path)
    assert info["shell"] == "powershell"
    assert info["command"].startswith("& 'C:\\Users\\O''Brien\\App Data\\python.exe'")
    assert "'--root'" in info["command"]


@pytest.mark.skipif(__import__("os").name == "nt", reason="executes the POSIX copyable command")
def test_copyable_status_command_does_not_inherit_terminal_mirror(tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    from narranexus.platform.browser._browser_impl.install import manual_install_help

    monkeypatch.delenv("NARRANEXUS_BROWSER_DOWNLOAD_HOST", raising=False)
    info = manual_install_help(root=tmp_path / "browser's files; $(touch unwanted)")
    process = subprocess.run(info["status_command"], shell=True, cwd=tmp_path,
                             env={**os.environ, "NARRANEXUS_BROWSER_DOWNLOAD_HOST": "https://other.example"},
                             capture_output=True, text=True, timeout=15)
    assert process.returncode == 0, process.stderr
    observed = json.loads(process.stdout)["manual_install"]
    assert observed["download_host"] is None
    assert observed["root"] == info["root"]
    assert not (tmp_path / "unwanted").exists()


def _run_cli(tmp_path, *arguments, env=None):
    import os
    import subprocess
    import sys

    return subprocess.run(
        [sys.executable, "-I", "-m", "narranexus.platform.browser", *arguments],
        cwd=tmp_path, env=env or {**os.environ, "NEXUS_DIAG_SHIP": "off"},
        text=True, capture_output=True, timeout=30,
    )


def test_manual_cli_status_and_idle_cancel_do_not_create_runtime(tmp_path):
    import json

    root = tmp_path / "user's browser; $(touch unwanted)"
    for action in ("status", "cancel"):
        process = _run_cli(tmp_path, action, "--root", str(root))
        assert process.returncode == 0, process.stderr
        result = json.loads(process.stdout)
        assert result["type"] == action
        if action == "status":
            assert result["status"]["state"] == "absent"
            assert result["manual_install"]["root"] == str(root)
        assert not root.exists()


def test_manual_cli_cancel_reaches_another_process_install(tmp_path):
    import json
    from narranexus.platform.browser._browser_impl.install import _InstallState, _atomic_json

    state = _InstallState(tmp_path / "runtime")
    assert state.acquire()
    try:
        _atomic_json(state.state_path, {"token": "attempt-from-ui"})
        process = _run_cli(tmp_path, "cancel", "--root", str(state.root))
        assert process.returncode == 0, process.stderr
        assert json.loads(process.stdout)["installing"]
        assert state.cancelled("attempt-from-ui")
    finally:
        state.release()


def test_manual_cli_rejects_plaintext_mirror_with_actionable_diagnostics(tmp_path):
    import json

    root = tmp_path / "runtime"
    process = _run_cli(tmp_path, "install", "--root", str(root),
                       "--download-host", "http://mirror.example")
    assert process.returncode == 1, process.stderr
    result = json.loads(process.stdout)
    assert not result["ok"]
    assert "https" in result["error"]
    assert result["manual_install"]["root"] == str(root)
    assert not root.exists()


@pytest.mark.skipif(__import__("os").name == "nt", reason="fixture executable uses a POSIX shell")
def test_manual_cli_installs_through_real_https_mirrors_and_preserves_profiles(tmp_path):
    import datetime
    import http.server
    import io
    import ipaddress
    import json
    import os
    import ssl
    import threading
    import zipfile

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    from narranexus.platform.browser._browser_impl.locate import platform_key

    plat = platform_key()
    relative = (f"chrome-{plat}/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
                if plat.startswith("mac-") else "chrome-linux64/chrome")
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr(relative, "#!/bin/sh\necho 'Google Chrome for Testing 154.0.8037.57'\n")
    payload = archive.getvalue()
    manifest = {"channels": {"Stable": {"version": "154.0.8037.57", "downloads": {"chrome": [
        {"platform": plat, "url": "https://vendor.invalid/154/browser.zip"},
    ]}}}}
    calls = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            if self.path == "/metadata/stable.json":
                data = json.dumps(manifest).encode()
            elif self.path == "/mirror/154/browser.zip":
                data = payload
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *_args):
            pass

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - datetime.timedelta(minutes=1))
                   .not_valid_after(now + datetime.timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([
                       x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                   ]), critical=False).sign(key, hashes.SHA256()))
    cert_file, key_file = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_file.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_file.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_file, key_file)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    root = tmp_path / "runtime with spaces"
    profile = root / "profiles" / "existing-session"
    profile.mkdir(parents=True)
    (profile / "sentinel").write_text("keep profile")
    base = f"https://127.0.0.1:{server.server_port}"
    env = {**os.environ, "SSL_CERT_FILE": str(cert_file), "NO_PROXY": "127.0.0.1", "NEXUS_DIAG_SHIP": "off",
           "NARRANEXUS_BROWSER_MANIFEST_URL": "https://wrong.invalid/index.json",
           "NARRANEXUS_BROWSER_DOWNLOAD_HOST": "https://wrong.invalid"}
    try:
        args = ("install", "--root", str(root), "--manifest-url", base + "/metadata/stable.json",
                "--download-host", base + "/mirror")
        process = _run_cli(tmp_path, *args, env=env)
        assert process.returncode == 0, process.stdout + process.stderr
        records = [json.loads(line) for line in process.stdout.splitlines()]
        assert records[-1]["ok"]
        assert records[-1]["status"]["state"] == "ready"
        assert {r["phase"] for r in records if r["type"] == "progress"} == {
            "downloading", "extracting", "verifying",
        }
        assert calls == ["/metadata/stable.json", "/mirror/154/browser.zip"]
        assert (profile / "sentinel").read_text() == "keep profile"
        again = _run_cli(tmp_path, *args, env=env)
        assert again.returncode == 0, again.stdout + again.stderr
        assert len(calls) == 2, "ready runtime must not be downloaded again"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
