"""Unit tests for NarraCliClient — the narra-cli spawn choke point.

Pins the security-critical mechanics: the bearer is injected via an EPHEMERAL
``--token-file`` (never on argv, never persisted), the file is removed after the
call (success AND failure), the agent workspace is threaded as CWD, and the
narra-cli JSON envelope is normalized.
"""
import json
import os
import stat
from pathlib import Path

import pytest

from xyz_agent_context.module.narramessenger_module import narra_cli_client as ncc
from xyz_agent_context.module.narramessenger_module.narra_cli_client import (
    NarraCliClient,
    _resolve_narra_cli,
)

BEARER = "secret-bearer-xyz"


class _FakeProc:
    def __init__(self, stdout: bytes, returncode: int = 0):
        self._stdout = stdout
        self.returncode = returncode

    async def communicate(self, input=None):
        return self._stdout, b""

    def kill(self):
        pass

    async def wait(self):
        return self.returncode


def _patch_exec(monkeypatch, capture: dict, envelope: dict, returncode: int = 0):
    """Patch create_subprocess_exec; record argv+cwd and assert the token-file
    exists (and holds the bearer) at spawn time."""
    async def fake_exec(*cmd, stdout=None, stderr=None, stdin=None, env=None, cwd=None):
        capture["cmd"] = list(cmd)
        capture["cwd"] = cwd
        capture["env"] = env
        # token-file must exist and contain the bearer WHILE the CLI runs.
        idx = cmd.index("--token-file")
        tok_path = cmd[idx + 1]
        capture["token_path"] = tok_path
        capture["token_existed_during"] = os.path.exists(tok_path)
        capture["token_content"] = Path(tok_path).read_text()
        capture["token_mode"] = stat.S_IMODE(os.stat(tok_path).st_mode)
        return _FakeProc(json.dumps(envelope).encode(), returncode)

    monkeypatch.setattr(ncc.asyncio, "create_subprocess_exec", fake_exec)
    # Pin the binary so resolution doesn't depend on the host.
    monkeypatch.setenv("NARRA_CLI_BIN", "/opt/narra-cli/node_modules/.bin/narra-cli")
    ncc._NARRA_CLI_BIN = None  # reset memoisation


async def test_token_file_injected_not_on_argv(monkeypatch):
    cap: dict = {}
    _patch_exec(monkeypatch, cap, {"command": "status", "data": {"ok": True}, "status": "ok"})
    client = NarraCliClient(BEARER)
    await client.run(["status"])

    assert "--token-file" in cap["cmd"]
    # The bearer itself must never appear as an argv token.
    assert BEARER not in cap["cmd"]
    # It lived in the file, containing exactly the bearer, mode 600.
    assert cap["token_existed_during"] is True
    assert cap["token_content"] == BEARER
    assert cap["token_mode"] == 0o600


async def test_token_file_removed_after_success(monkeypatch):
    cap: dict = {}
    _patch_exec(monkeypatch, cap, {"command": "status", "data": {}, "status": "ok"})
    client = NarraCliClient(BEARER)
    await client.run(["status"])
    assert not os.path.exists(cap["token_path"])  # cleaned up


async def test_token_file_removed_after_exception(monkeypatch):
    seen: dict = {}

    async def boom(*cmd, **kw):
        idx = cmd.index("--token-file")
        seen["token_path"] = cmd[idx + 1]
        raise RuntimeError("spawn blew up")

    monkeypatch.setattr(ncc.asyncio, "create_subprocess_exec", boom)
    monkeypatch.setenv("NARRA_CLI_BIN", "/opt/narra-cli/node_modules/.bin/narra-cli")
    ncc._NARRA_CLI_BIN = None
    client = NarraCliClient(BEARER)
    with pytest.raises(RuntimeError):
        await client.run(["status"])
    assert not os.path.exists(seen["token_path"])  # cleaned up even on crash


async def test_home_redirected_so_narra_cli_can_chmod_its_config_dir(monkeypatch):
    # narra-cli chmods $HOME/.narra-cli on startup; the real container HOME is on
    # a mount the server user can't chmod (EPERM). We must point HOME at a
    # process-owned, writable dir.
    ncc._NARRA_HOME = None  # force fresh creation
    cap: dict = {}
    _patch_exec(monkeypatch, cap, {"command": "status", "data": {}, "status": "ok"})
    await NarraCliClient(BEARER).run(["status"])
    home = cap["env"]["HOME"]
    assert home == ncc._narra_cli_home()
    assert os.path.isdir(home)           # created + writable
    assert os.access(home, os.W_OK)
    assert home != os.path.expanduser("~")  # NOT the real (unchmod-able) HOME
    # Owned by us + private (no co-tenant squat / config leak on a shared host).
    st = os.stat(home)
    assert st.st_uid == os.getuid()
    assert stat.S_IMODE(st.st_mode) == 0o700
    assert str(os.getuid()) in home       # per-uid path


async def test_cwd_threaded(monkeypatch):
    cap: dict = {}
    _patch_exec(monkeypatch, cap, {"command": "im send", "data": {}, "status": "ok"})
    client = NarraCliClient(BEARER)
    await client.run(["im", "send"], cwd="/work/agent_x")
    assert cap["cwd"] == "/work/agent_x"


async def test_ok_envelope_normalized(monkeypatch):
    cap: dict = {}
    _patch_exec(monkeypatch, cap, {"command": "status", "data": {"room": 3}, "status": "ok"})
    client = NarraCliClient(BEARER)
    out = await client.run(["status"])
    assert out["success"] is True
    assert out["data"] == {"room": 3}


async def test_error_envelope_normalized(monkeypatch):
    cap: dict = {}
    envelope = {
        "command": "im send",
        "data": None,
        "issues": [{"code": "agent-room-access-denied", "message": "no"}],
        "status": "error",
    }
    _patch_exec(monkeypatch, cap, envelope, returncode=1)
    client = NarraCliClient(BEARER)
    out = await client.run(["im", "send"])
    assert out["success"] is False
    assert out["error"] == "agent-room-access-denied"


async def test_empty_stdout_exit0_is_success(monkeypatch):
    # A file-writing command (speech synthesize --out / attachments download
    # --output) can succeed with empty stdout — exit 0 must map to success,
    # NOT a false "empty_output" failure.
    async def fake_exec(*cmd, **kw):
        return _FakeProc(b"", returncode=0)

    monkeypatch.setattr(ncc.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("NARRA_CLI_BIN", "/opt/narra-cli/node_modules/.bin/narra-cli")
    ncc._NARRA_CLI_BIN = None
    out = await NarraCliClient(BEARER).run(["speech", "synthesize", "--out", "./r.wav"])
    assert out["success"] is True


async def test_empty_stdout_nonzero_is_failure(monkeypatch):
    async def fake_exec(*cmd, **kw):
        return _FakeProc(b"", returncode=1)

    monkeypatch.setattr(ncc.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setenv("NARRA_CLI_BIN", "/opt/narra-cli/node_modules/.bin/narra-cli")
    ncc._NARRA_CLI_BIN = None
    out = await NarraCliClient(BEARER).run(["speech", "synthesize"])
    assert out["success"] is False
    assert out["error"] == "empty_output"


def test_managed_install_beats_stale_global_on_path(monkeypatch):
    # A stale global `narra-cli` on PATH must NOT shadow our managed install.
    monkeypatch.delenv("NARRA_CLI_BIN", raising=False)
    ncc._NARRA_CLI_BIN = None
    managed = ncc._MANAGED_INSTALL_BINS[0]
    monkeypatch.setattr(ncc.os.path, "isfile", lambda p: p == managed)
    monkeypatch.setattr(ncc.os, "access", lambda p, m: p == managed)
    called = {"which": False}

    def fake_which(_name):
        called["which"] = True
        return "/opt/homebrew/bin/narra-cli"  # the stale global

    monkeypatch.setattr(ncc.shutil, "which", fake_which)
    resolved, _extra = ncc._resolve_narra_cli()
    assert resolved == managed
    assert called["which"] is False  # never fell through to PATH


def test_resolve_prefers_env_override(monkeypatch):
    monkeypatch.setenv("NARRA_CLI_BIN", "/custom/narra-cli")
    ncc._NARRA_CLI_BIN = None
    # Pretend the path is executable so resolution accepts it.
    monkeypatch.setattr(ncc.os, "access", lambda p, m: p == "/custom/narra-cli")
    monkeypatch.setattr(ncc.os.path, "isfile", lambda p: p == "/custom/narra-cli")
    resolved, _extra = _resolve_narra_cli()
    assert resolved == "/custom/narra-cli"
