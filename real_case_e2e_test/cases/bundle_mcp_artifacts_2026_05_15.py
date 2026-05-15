"""
@file_name: bundle_mcp_artifacts_2026_05_15.py
@author: Bin Liang
@date: 2026-05-15
@description: Live e2e — two-user bundle export/import for the 1.1
    Skills & MCP + Artifacts release.

Plan (driven exclusively through HTTP, no DB tweaks):

1.  Boot a backend on an isolated DB + workspace root (parent script does this).
2.  Create users `binliang` (A) and `e2e_userB` (B).
3.  As A:
    a. POST /api/auth/agents → create an agent.
    b. Drop a real file under that agent's workspace (`{wsroot}/{aid}_{A}/work/output.html`).
    c. POST /api/agents/{aid}/artifacts/register → register the file.
    d. POST /api/agents/{aid}/mcps?user_id=A → register an MCP URL.
4.  As A: POST /api/bundle/export with artifact_selection={aid: [art_id]} and
    mcp_selection={aid: [mcp_id]}. Save the .nxbundle locally.
5.  As B: multipart POST /api/bundle/import/preflight, then POST /confirm.
6.  Verify B's state:
    a. GET /api/auth/agents (X-User-Id=B) lists exactly one agent (the imported one).
    b. Direct SQLite SELECT on instance_artifacts confirms:
        - file_path starts with `{new_aid}_{B}/work/output.html`
        - session_id IS NULL, pinned = 1, user_id = B
    c. SELECT on mcp_urls confirms:
        - one row, agent_id=new_aid, user_id=B
        - mcp_id rewritten (not the source mcp_id)
        - connection_status / last_error reset (NULL)
    d. File on disk exists at `{wsroot}/{new_aid}_{B}/work/output.html`.

This script is intentionally fail-fast: each step asserts before moving on so
a regression shows up at the first broken contract, not as cascading noise.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx


BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8101")
SQLITE_PATH = os.environ.get("E2E_SQLITE_PATH", "/tmp/nx-e2e-bundle/test.db")
WORKSPACE_ROOT = os.environ.get("E2E_WORKSPACE_ROOT", "/tmp/nx-e2e-bundle/workspaces")

USER_A = "binliang"
USER_B = "e2e_userB"


def _h(user_id: str) -> Dict[str, str]:
    """Headers for local-mode identity. Backend reads X-User-Id off every /api/* call."""
    return {"X-User-Id": user_id, "Content-Type": "application/json"}


def _req(method: str, path: str, *, user_id: Optional[str] = None, expect: int = 200,
         json_body: Any = None, params: Optional[Dict[str, Any]] = None,
         files=None, data=None) -> Dict[str, Any]:
    """Single request helper that fails loudly on unexpected status codes.

    The bundle import endpoints are multipart, so we accept `files`/`data`
    overrides; everything else goes through JSON.
    """
    url = f"{BASE_URL}{path}"
    headers: Dict[str, str] = {}
    if user_id is not None:
        headers["X-User-Id"] = user_id
    if files is None and json_body is not None:
        headers["Content-Type"] = "application/json"
    with httpx.Client(timeout=60) as cx:
        resp = cx.request(method, url, headers=headers, json=json_body,
                          params=params, files=files, data=data)
    if resp.status_code != expect:
        raise AssertionError(
            f"{method} {path} expected HTTP {expect}, got {resp.status_code}\n"
            f"body: {resp.text[:1000]}"
        )
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    return {"_raw_text": resp.text, "_raw_bytes": resp.content,
            "_status_code": resp.status_code, "_headers": dict(resp.headers)}


def wait_for_backend(deadline_s: float = 30) -> None:
    """The parent script may launch the backend in the background; poll
    /openapi.json until it responds. Aborts on timeout so we don't sit in
    test purgatory."""
    end = time.time() + deadline_s
    last_err = None
    while time.time() < end:
        try:
            r = httpx.get(f"{BASE_URL}/openapi.json", timeout=2)
            if r.status_code == 200:
                return
        except Exception as e:
            last_err = e
        time.sleep(0.5)
    raise RuntimeError(f"Backend not reachable on {BASE_URL}: {last_err}")


def ensure_user(user_id: str) -> None:
    """create-user is idempotent-friendly: it returns success=False when the
    user already exists, which we tolerate. Anything else is a real error."""
    r = _req("POST", "/api/auth/create-user", json_body={
        "user_id": user_id,
        "display_name": user_id,
    })
    if r.get("success"):
        print(f"  · created user {user_id}")
    elif "already exists" in (r.get("error") or ""):
        print(f"  · user {user_id} already present")
    else:
        raise AssertionError(f"create-user {user_id} failed: {r}")


def main() -> None:
    print(f"[setup] base_url={BASE_URL} sqlite={SQLITE_PATH} ws={WORKSPACE_ROOT}")
    wait_for_backend()
    print("[setup] backend up")

    # ── Step 1 — users ────────────────────────────────────────────────────
    print("[step 1] create users")
    ensure_user(USER_A)
    ensure_user(USER_B)

    # ── Step 2 — A creates an agent ───────────────────────────────────────
    print("[step 2] create agent for A")
    create_r = _req("POST", "/api/auth/agents", user_id=USER_A, json_body={
        "agent_name": "E2E Bundle Test Agent",
        "agent_description": "fixture for the 1.1 bundle e2e",
        "created_by": USER_A,
    })
    assert create_r.get("success"), create_r
    aid = create_r["agent"]["agent_id"]
    print(f"  · agent_id = {aid}")

    # ── Step 3 — drop a file in A's workspace and register it as an artifact ─
    print("[step 3] write file + register artifact")
    workspace = Path(WORKSPACE_ROOT) / f"{aid}_{USER_A}"
    art_dir = workspace / "work"
    art_dir.mkdir(parents=True, exist_ok=True)
    entry_file = art_dir / "output.html"
    body_html = "<!doctype html><html><body><h1>e2e payload</h1></body></html>"
    entry_file.write_text(body_html, encoding="utf-8")
    print(f"  · wrote {entry_file} ({entry_file.stat().st_size} B)")

    reg_r = _req("POST", f"/api/agents/{aid}/artifacts/register", user_id=USER_A,
                 json_body={
                     "file_path": "work/output.html",  # workspace-relative
                     "kind": "text/html",
                     "title": "E2E hello",
                     "description": "registered for bundle export",
                 })
    art_id = reg_r["artifact_id"]
    src_file_path = reg_r["file_path"]
    print(f"  · artifact_id = {art_id}  file_path (DB-side) = {src_file_path}")
    assert src_file_path.startswith(f"{aid}_{USER_A}/"), (
        f"DB file_path should be relative to base_working_path with the "
        f"agent prefix; got {src_file_path!r}"
    )

    # ── Step 4 — register an MCP URL on the agent ─────────────────────────
    print("[step 4] register MCP url")
    mcp_r = _req("POST", f"/api/agents/{aid}/mcps", user_id=USER_A,
                 params={"user_id": USER_A},
                 json_body={
                     "name": "E2E MCP",
                     "url": "https://example.invalid/mcp/e2e",
                     "description": "fixture MCP for export",
                     "is_enabled": True,
                 })
    assert mcp_r.get("success"), mcp_r
    src_mcp_id = mcp_r["mcp"]["mcp_id"]
    print(f"  · mcp_id = {src_mcp_id}")

    # ── Step 5 — export bundle as A ───────────────────────────────────────
    print("[step 5] export bundle (with artifact + mcp selection)")
    export_payload = {
        "agent_ids": [aid],
        "include_chat_history": False,
        "artifact_selection": {aid: [art_id]},
        "mcp_selection": {aid: [src_mcp_id]},
    }
    # /export returns a stream; bypass _req's JSON expectation
    with httpx.Client(timeout=120) as cx:
        resp = cx.post(f"{BASE_URL}/api/bundle/export",
                       headers={"X-User-Id": USER_A, "Content-Type": "application/json"},
                       json=export_payload)
    assert resp.status_code == 200, f"export failed: HTTP {resp.status_code}\n{resp.text[:500]}"
    bundle_bytes = resp.content
    bundle_size = len(bundle_bytes)
    print(f"  · received {bundle_size} bytes of .nxbundle")
    bundle_path = Path("/tmp/nx-e2e-bundle/e2e.nxbundle")
    bundle_path.write_bytes(bundle_bytes)

    # ── Step 6 — import as B ──────────────────────────────────────────────
    print("[step 6] preflight + confirm as B")
    with httpx.Client(timeout=120) as cx:
        files = {"file": ("e2e.nxbundle", bundle_bytes, "application/zip")}
        pf = cx.post(f"{BASE_URL}/api/bundle/import/preflight",
                     headers={"X-User-Id": USER_B},
                     files=files)
    assert pf.status_code == 200, f"preflight failed: HTTP {pf.status_code}\n{pf.text[:500]}"
    preflight = pf.json()
    token = preflight["preflight_token"]
    print(f"  · preflight_token = {token[:12]}…")
    conf = _req("POST", "/api/bundle/import/confirm", user_id=USER_B,
                json_body={"preflight_token": token})
    print(f"  · confirm summary: agents={conf.get('agents_created')} "
          f"artifacts={conf.get('artifacts_created')} mcps={conf.get('mcp_urls_created')}")
    assert conf.get("agents_created") == 1, conf
    assert conf.get("artifacts_created") == 1, (
        f"expected 1 artifact created, got summary={conf}"
    )
    assert conf.get("mcp_urls_created") == 1, (
        f"expected 1 mcp_url created, got summary={conf}"
    )

    # ── Step 7 — direct DB assertions on B's side ─────────────────────────
    print("[step 7] verify B's data")
    con = sqlite3.connect(SQLITE_PATH)
    con.row_factory = sqlite3.Row
    try:
        # Agent
        new_agents = [dict(r) for r in con.execute(
            "SELECT agent_id, agent_name FROM agents WHERE created_by = ?",
            (USER_B,)
        )]
        assert len(new_agents) == 1, f"expected 1 agent for B, got {new_agents}"
        new_aid = new_agents[0]["agent_id"]
        assert new_aid != aid, f"agent_id wasn't rewritten ({new_aid} == {aid})"
        print(f"  · new agent_id = {new_aid} (name = {new_agents[0]['agent_name']})")

        # Artifact
        arts = [dict(r) for r in con.execute(
            "SELECT artifact_id, file_path, session_id, pinned, user_id "
            "FROM instance_artifacts WHERE agent_id = ?", (new_aid,)
        )]
        assert len(arts) == 1, f"expected 1 artifact, got {arts}"
        art = arts[0]
        expected_prefix = f"{new_aid}_{USER_B}/"
        assert art["file_path"] == f"{expected_prefix}work/output.html", (
            f"file_path wrong; expected '{expected_prefix}work/output.html', got {art['file_path']!r}"
        )
        assert art["session_id"] is None, f"session_id should be NULL; got {art['session_id']!r}"
        assert int(art["pinned"]) == 1, f"pinned should be 1; got {art['pinned']!r}"
        assert art["user_id"] == USER_B, f"user_id should be {USER_B!r}; got {art['user_id']!r}"
        assert art["artifact_id"] != art_id, f"artifact_id wasn't rewritten ({art['artifact_id']})"
        print(f"  · artifact {art['artifact_id']} OK (file_path={art['file_path']})")

        # MCP
        mcps = [dict(r) for r in con.execute(
            "SELECT mcp_id, name, url, user_id, connection_status, last_error, is_enabled "
            "FROM mcp_urls WHERE agent_id = ?", (new_aid,)
        )]
        assert len(mcps) == 1, f"expected 1 mcp_url, got {mcps}"
        mcp = mcps[0]
        assert mcp["mcp_id"] != src_mcp_id, f"mcp_id not rewritten ({mcp['mcp_id']})"
        assert mcp["name"] == "E2E MCP", mcp
        assert mcp["url"] == "https://example.invalid/mcp/e2e", mcp
        assert mcp["user_id"] == USER_B, mcp
        assert (mcp["connection_status"] in (None, "")), (
            f"connection_status should be reset to NULL, got {mcp['connection_status']!r}"
        )
        assert (mcp["last_error"] in (None, "")), (
            f"last_error should be reset to NULL, got {mcp['last_error']!r}"
        )
        assert int(mcp["is_enabled"]) == 1, mcp
        print(f"  · mcp {mcp['mcp_id']} OK (status={mcp['connection_status']!r})")
    finally:
        con.close()

    # File on disk
    disk_path = Path(WORKSPACE_ROOT) / f"{new_aid}_{USER_B}" / "work" / "output.html"
    assert disk_path.exists(), f"expected workspace file at {disk_path}"
    on_disk = disk_path.read_text(encoding="utf-8")
    assert "e2e payload" in on_disk, (
        f"workspace file content mismatch; got first 80 bytes = {on_disk[:80]!r}"
    )
    print(f"  · workspace file present: {disk_path}")

    print("\n[PASS] bundle 1.1 (Skills & MCP + Artifacts) e2e roundtrip OK")


if __name__ == "__main__":
    try:
        main()
    except AssertionError as e:
        print(f"\n[FAIL] {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
