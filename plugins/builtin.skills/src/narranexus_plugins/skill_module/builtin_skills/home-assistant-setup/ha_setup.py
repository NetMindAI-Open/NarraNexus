#!/usr/bin/env python3
"""
@file_name: ha_setup.py
@author: NetMind.AI
@date: 2026-07-16
@description: One-command Home Assistant + Xiaomi onboarding CLI for the
`home-assistant-setup` built-in skill. An agent runs this instead of a dozen
hand-typed docker/curl commands.

What it automates (local/desktop only — cloud users bring their own HA):
  deploy      Docker-deploy Home Assistant, wait until ready
  init        Onboard the admin account + mint a Long-Lived Access Token
  install-xiaomi   Install the official `XiaoMi/ha_xiaomi_home` integration
  xiaomi-login     Start the Xiaomi config flow, emit the OAuth login URL
  xiaomi-callback  Complete OAuth without /etc/hosts (paste back the callback URL)
  xiaomi-finish    Finish the flow when the callback reached HA directly
  hosts       (optional) Make `homeassistant.local` resolve locally
  bind        Write base_url+token into NarraNexus (optional convenience)
  doctor      Read-only diagnosis of the whole chain
  all         Orchestrate the above, pausing at the one human step

Only ONE step truly needs a human: signing into Xiaomi + approving OAuth. The
CLI never fakes it — `xiaomi-login` prints an `ACTION_REQUIRED:` line and exits
10 with the login URL for the agent to relay.

Approach A (default, NO sudo): Xiaomi's callback redirects the browser to
http://homeassistant.local:8123/api/webhook/<id>?code=… which won't open (that
host doesn't resolve). But HA routes webhooks by path and ignores the Host
header, so the user pastes that address-bar URL back and `xiaomi-callback` swaps
the host to our reachable base_url to deliver the code. The old `hosts` (sudo)
route still exists for users who prefer the browser to land back on HA itself,
after which they run `xiaomi-finish`.

Design notes:
  - Self-contained: only stdlib + aiohttp (aiohttp used solely for the one
    WebSocket call that mints the Long-Lived token — HA exposes no REST for it).
  - Idempotent: every step detects "already done" and skips.
  - State in ~/.nn_ha_setup/state.json (mode 600) carries base_url / access_token
    / llat / flow_id across invocations, so the human pause can resume.
  - The Xiaomi OAuth redirect_uri is locked to http://homeassistant.local:8123 on
    Xiaomi's side (registered for ha_xiaomi_home's OAuth app); it CANNOT be
    changed to localhost. Approach A replays the callback past it; the optional
    `hosts` step instead makes that fixed URL resolve locally.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HA_IMAGE = "ghcr.io/home-assistant/home-assistant:stable"
DEFAULT_CONTAINER = "homeassistant"
DEFAULT_PORT = 8123
XIAOMI_REPO = "XiaoMi/ha_xiaomi_home"
# Xiaomi registered exactly this redirect for ha_xiaomi_home's OAuth app; it is
# the only accepted callback host. Approach A replays the callback past it;
# the optional `hosts` step instead makes it resolve locally.
XIAOMI_OAUTH_HOST = "homeassistant.local"

STATE_DIR = Path.home() / ".nn_ha_setup"
STATE_FILE = STATE_DIR / "state.json"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_ACTION_REQUIRED = 10


# ---------------------------------------------------------------------------
# Output helpers — human lines to stderr, one machine RESULT line per command
# to stdout. `all` chains several commands, so it prints several RESULT lines;
# in that case the LAST RESULT line is the authoritative outcome.
# ---------------------------------------------------------------------------


def log(msg: str) -> None:
    """Print a human-readable progress line (to stderr, keeps stdout clean)."""
    print(msg, file=sys.stderr, flush=True)


def emit_result(**fields: Any) -> None:
    """Print the machine-parseable result line the agent reads from stdout."""
    print("RESULT " + json.dumps(fields, ensure_ascii=False), flush=True)


def action_required(message: str, **fields: Any) -> None:
    """Print the human-action marker and exit 10 so the agent pauses + relays it."""
    log(f"\nACTION_REQUIRED: {message}\n")
    emit_result(status="action_required", message=message, **fields)
    sys.exit(EXIT_ACTION_REQUIRED)


def fail(message: str, **fields: Any) -> None:
    """Print an error result and exit 1."""
    log(f"ERROR: {message}")
    emit_result(status="error", error=message, **fields)
    sys.exit(EXIT_ERROR)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


def load_state() -> dict[str, Any]:
    """Read the persisted cross-invocation state, or {} if none."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except Exception:  # noqa: BLE001 — corrupt state should not crash the CLI
            return {}
    return {}


def save_state(state: dict[str, Any]) -> None:
    """Persist state with 600 perms (it holds tokens)."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2, ensure_ascii=False))
    try:
        os.chmod(STATE_FILE, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# HTTP (stdlib) — HA REST + onboarding are plain JSON/form endpoints
# ---------------------------------------------------------------------------


def _http(
    method: str,
    url: str,
    *,
    token: Optional[str] = None,
    json_body: Optional[dict] = None,
    form_body: Optional[dict] = None,
    timeout: int = 30,
) -> tuple[int, Any]:
    """Do one HTTP request. Returns (status_code, parsed_json_or_text).

    Returns status 0 on a connection-level failure (host unreachable).
    """
    headers = {}
    data: Optional[bytes] = None
    if json_body is not None:
        data = json.dumps(json_body).encode()
        headers["Content-Type"] = "application/json"
    elif form_body is not None:
        data = urllib.parse.urlencode(form_body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _maybe_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, _maybe_json(raw)
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, None


def _maybe_json(raw: str) -> Any:
    """Parse JSON if possible, else return the raw string."""
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw


def ha_ready(base_url: str, timeout: int = 3) -> bool:
    """True if HA answers on /api/onboarding (open pre-auth) — proxy for 'up'."""
    code, _ = _http("GET", f"{base_url}/api/onboarding", timeout=timeout)
    # 200 = onboarding pending; 404 = onboarding done (endpoint closed). Both = up.
    return code in (200, 404)


def wait_ready(base_url: str, timeout_s: int = 180) -> bool:
    """Poll until HA is up or timeout. Returns True if it came up."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if ha_ready(base_url):
            return True
        time.sleep(2)
    return False


# ---------------------------------------------------------------------------
# Docker helpers
# ---------------------------------------------------------------------------


def _docker(*args: str, check: bool = False, capture: bool = True) -> subprocess.CompletedProcess:
    """Run a docker command."""
    return subprocess.run(
        ["docker", *args],
        check=check,
        capture_output=capture,
        text=True,
    )


def docker_available() -> bool:
    """True if the docker CLI works."""
    try:
        return _docker("version", "--format", "{{.Server.Version}}").returncode == 0
    except FileNotFoundError:
        return False


def container_state(name: str) -> Optional[str]:
    """Return the container's state ('running'/'exited'/…) or None if absent."""
    r = _docker("inspect", "-f", "{{.State.Status}}", name)
    return r.stdout.strip() if r.returncode == 0 else None


# ---------------------------------------------------------------------------
# WebSocket (aiohttp) — the only step with no REST equivalent
# ---------------------------------------------------------------------------


async def _mint_llat_async(base_url: str, access_token: str, client_name: str) -> Optional[str]:
    """Create a Long-Lived Access Token via the HA WebSocket API."""
    import aiohttp

    ws_url = base_url.replace("http", "ws", 1) + "/api/websocket"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=30)) as ws:
            await ws.receive_json()  # auth_required
            await ws.send_json({"type": "auth", "access_token": access_token})
            if (await ws.receive_json()).get("type") != "auth_ok":
                return None
            await ws.send_json(
                {
                    "id": 1,
                    "type": "auth/long_lived_access_token",
                    "client_name": client_name,
                    "lifespan": 3650,
                }
            )
            res = await ws.receive_json()
            return res.get("result") if res.get("success") else None


def mint_llat(base_url: str, access_token: str, client_name: str = "NarraNexus") -> Optional[str]:
    """Sync wrapper around the WS long-lived-token mint."""
    return asyncio.run(_mint_llat_async(base_url, access_token, client_name))


async def _config_core_update_async(base_url: str, token: str, internal_url: str) -> bool:
    """Set HA internal/external URL via WS (best-effort)."""
    import aiohttp

    ws_url = base_url.replace("http", "ws", 1) + "/api/websocket"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(ws_url, timeout=aiohttp.ClientTimeout(total=20)) as ws:
            await ws.receive_json()
            await ws.send_json({"type": "auth", "access_token": token})
            if (await ws.receive_json()).get("type") != "auth_ok":
                return False
            await ws.send_json(
                {
                    "id": 1,
                    "type": "config/core/update",
                    "internal_url": internal_url,
                    "external_url": internal_url,
                }
            )
            return bool((await ws.receive_json()).get("success"))


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def cmd_deploy(args: argparse.Namespace) -> None:
    """Docker-deploy Home Assistant (idempotent) and wait until it is ready."""
    if not docker_available():
        fail("Docker is not available. Install Docker Desktop / engine first.")

    base_url = f"http://localhost:{args.port}"
    st = load_state()
    st["base_url"] = base_url
    st["container"] = args.container

    state = container_state(args.container)
    if state == "running":
        log(f"Container '{args.container}' already running — skipping deploy.")
    elif state in ("exited", "created", "paused"):
        log(f"Container '{args.container}' exists ({state}) — starting it.")
        _docker("start", args.container)
    else:
        log(f"Deploying Home Assistant container '{args.container}' on port {args.port}…")
        cfg_dir = STATE_DIR / "ha-config"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        r = _docker(
            "run",
            "-d",
            "--name",
            args.container,
            "--restart",
            "unless-stopped",
            "-v",
            f"{cfg_dir}:/config",
            "-p",
            f"{args.port}:8123",
            HA_IMAGE,
            check=False,
        )
        if r.returncode != 0:
            fail(f"docker run failed: {r.stderr.strip()}")

    save_state(st)
    log("Waiting for HA to become ready…")
    if not wait_ready(base_url):
        fail("HA did not become ready in time.", base_url=base_url)
    log("HA is up.")
    emit_result(status="ok", base_url=base_url, container=args.container)


def _onboarding_steps(base_url: str) -> dict[str, bool]:
    """Return {step: done} from /api/onboarding, or {} if already fully onboarded."""
    code, body = _http("GET", f"{base_url}/api/onboarding")
    if code == 200 and isinstance(body, list):
        return {s["step"]: s["done"] for s in body}
    return {}  # 404 → onboarding already complete


def cmd_init(args: argparse.Namespace) -> None:
    """Create the admin account (if needed) and mint a Long-Lived Access Token."""
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{args.port}")
    st["base_url"] = base_url
    client_id = base_url + "/"

    if not ha_ready(base_url):
        fail("HA is not reachable. Run `deploy` first.", base_url=base_url)

    steps = _onboarding_steps(base_url)
    access_token: Optional[str] = None
    generated_password: Optional[str] = None

    if steps.get("user") is False:
        # Fresh install → create the owner account.
        password = args.password
        if args.gen or not password:
            password = "HA-" + secrets.token_hex(8)
            generated_password = password
        log(f"Creating admin account '{args.username}'…")
        code, body = _http(
            "POST",
            f"{base_url}/api/onboarding/users",
            json_body={
                "client_id": client_id,
                "name": args.name,
                "username": args.username,
                "password": password,
                "language": args.language,
            },
        )
        if code not in (200, 201) or not isinstance(body, dict) or "auth_code" not in body:
            fail(f"Failed to create admin user (HTTP {code}): {body}")
        auth_code = body["auth_code"]
        code, tok = _http(
            "POST",
            f"{base_url}/auth/token",
            form_body={"client_id": client_id, "grant_type": "authorization_code", "code": auth_code},
        )
        if code != 200 or not isinstance(tok, dict) or "access_token" not in tok:
            fail(f"Failed to exchange auth_code for token (HTTP {code}): {tok}")
        access_token = tok["access_token"]
        st["username"] = args.username
    else:
        # Already onboarded → need credentials to log in and mint a fresh token.
        if not args.password:
            fail(
                "HA is already onboarded. Provide --username and --password to "
                "mint a token, or reuse an existing Long-Lived token.",
                already_onboarded=True,
            )
        log("HA already onboarded — logging in to mint a token…")
        access_token = _login_for_token(base_url, client_id, args.username, args.password)
        if not access_token:
            fail("Login failed — wrong username/password?")
        st["username"] = args.username

    # Best-effort: finish the remaining onboarding steps so HA stops nagging.
    for step in ("analytics", "core_config"):
        _http("POST", f"{base_url}/api/onboarding/{step}", token=access_token, json_body={})

    log("Minting a Long-Lived Access Token…")
    # HA rejects a duplicate client_name, so make each token's name unique.
    client_name = f"NarraNexus ha-setup {secrets.token_hex(3)}"
    llat = mint_llat(base_url, access_token, client_name=client_name)
    if not llat:
        fail("Failed to mint the Long-Lived Access Token.")

    st["access_token"] = access_token
    st["llat"] = llat
    save_state(st)

    log("\n✅ Home Assistant account ready.")
    log(f"   URL:      {base_url}")
    log(f"   Username: {st.get('username')}")
    if generated_password:
        log(f"   Password: {generated_password}")
    log("   Long-Lived Token: (in RESULT line)")
    emit_result(
        status="ok",
        base_url=base_url,
        username=st.get("username"),
        password=generated_password,  # only present when generated
        token=llat,
    )


def _login_for_token(base_url: str, client_id: str, username: str, password: str) -> Optional[str]:
    """Run HA's login flow with username/password → access_token, or None."""
    code, flow = _http(
        "POST",
        f"{base_url}/auth/login_flow",
        json_body={"client_id": client_id, "handler": ["homeassistant", None], "redirect_uri": client_id},
    )
    if code != 200 or not isinstance(flow, dict):
        return None
    fid = flow.get("flow_id")
    code, res = _http(
        "POST",
        f"{base_url}/auth/login_flow/{fid}",
        json_body={"client_id": client_id, "username": username, "password": password},
    )
    if code != 200 or not isinstance(res, dict) or res.get("type") != "create_entry":
        return None
    auth_code = res.get("result")
    code, tok = _http(
        "POST",
        f"{base_url}/auth/token",
        form_body={"client_id": client_id, "grant_type": "authorization_code", "code": auth_code},
    )
    if code == 200 and isinstance(tok, dict):
        return tok.get("access_token")
    return None


def cmd_install_xiaomi(args: argparse.Namespace) -> None:
    """Install the official ha_xiaomi_home integration into the HA container."""
    st = load_state()
    container = st.get("container", DEFAULT_CONTAINER)
    base_url = st.get("base_url", f"http://localhost:{DEFAULT_PORT}")

    if container_state(container) is None:
        fail(f"HA container '{container}' not found. Run `deploy` first.")

    # Idempotent: skip if already present (unless --force).
    check = _docker(
        "exec",
        container,
        "sh",
        "-c",
        "test -d /config/custom_components/xiaomi_home && echo yes || echo no",
    )
    if check.stdout.strip() == "yes" and not args.force:
        log("xiaomi_home already installed — skipping.")
        emit_result(status="ok", installed=True, skipped=True)
        return

    log("Downloading ha_xiaomi_home inside the container (bypasses host GitHub blocks)…")
    install_sh = (
        "set -e; cd /tmp; "
        "TAG=$(curl -s -m 10 https://api.github.com/repos/XiaoMi/ha_xiaomi_home/releases/latest "
        '| python3 -c "import sys,json;print(json.load(sys.stdin).get(\\"tag_name\\",\\"\\"))" 2>/dev/null); '
        'if [ -n "$TAG" ]; then URL="https://github.com/XiaoMi/ha_xiaomi_home/archive/refs/tags/$TAG.zip"; '
        'else URL="https://github.com/XiaoMi/ha_xiaomi_home/archive/refs/heads/main.zip"; fi; '
        'echo "downloading $URL"; curl -L -m 90 -o x.zip "$URL"; '
        'python3 -c "import zipfile;zipfile.ZipFile(\\"x.zip\\").extractall(\\".\\")"; '
        'SRC=$(find . -maxdepth 1 -type d -name "ha_xiaomi_home-*" | head -1); '
        "mkdir -p /config/custom_components; "
        'cp -r "$SRC/custom_components/xiaomi_home" /config/custom_components/; '
        "python3 -c \"import json;print('installed', json.load(open('/config/custom_components/xiaomi_home/manifest.json'))['version'])\""
    )
    r = _docker("exec", container, "sh", "-c", install_sh, check=False)
    if r.returncode != 0 or "installed" not in r.stdout:
        fail(f"Xiaomi integration install failed: {r.stdout.strip()} {r.stderr.strip()}")
    log(r.stdout.strip())

    log("Restarting HA to load the integration…")
    _docker("restart", container)
    if not wait_ready(base_url, timeout_s=180):
        fail("HA did not come back after restart.")
    log("HA back up with xiaomi_home loaded.")
    emit_result(status="ok", installed=True)


def _hosts_resolves() -> bool:
    """True if homeassistant.local resolves to a loopback address."""
    try:
        ip = socket.gethostbyname(XIAOMI_OAUTH_HOST)
        return ip.startswith("127.")
    except OSError:
        return False


def cmd_hosts(args: argparse.Namespace) -> None:
    """Ensure homeassistant.local resolves to 127.0.0.1 (Xiaomi OAuth callback).

    Order of attempts:
      1. Already resolves → done.
      2. Running as root → write directly.
      3. Interactive terminal → run `sudo` so the user types their password here.
      4. No terminal (e.g. an agent's non-interactive bash) → cannot prompt for a
         password, so print the exact command as ACTION_REQUIRED for the user.
    `sudo` deliberately prompts (never `-n`/NOPASSWD): the password IS the user's
    authorization for this system write — the CLI must not bypass it.
    """
    if _hosts_resolves():
        log(f"{XIAOMI_OAUTH_HOST} already resolves locally — OK.")
        emit_result(status="ok", hosts_ok=True)
        return

    entry = f"127.0.0.1 {XIAOMI_OAUTH_HOST}"
    cmd = f"sudo sh -c 'echo \"{entry}\" >> /etc/hosts'"

    if os.geteuid() == 0:  # already root → write directly
        with open("/etc/hosts", "a") as f:
            f.write(f"\n{entry}\n")
        log(f"Added '{entry}' to /etc/hosts.")
        emit_result(status="ok", hosts_ok=True)
        return

    # A terminal is present → sudo can prompt for the password interactively.
    if sys.stdin.isatty() and sys.stderr.isatty():
        log("Adding the hosts entry (sudo will ask for your password)…")
        try:
            r = subprocess.run(["sudo", "sh", "-c", f'echo "{entry}" >> /etc/hosts'], check=False)
        except (OSError, KeyboardInterrupt):
            r = None
        if r is not None and r.returncode == 0 and _hosts_resolves():
            log(f"Added '{entry}' to /etc/hosts.")
            emit_result(status="ok", hosts_ok=True)
            return
        log("sudo did not complete — falling back to the manual command.")

    # No terminal (agent context) or sudo failed → hand the command to the user.
    action_required(
        f"Please run this once (needs sudo — only you can edit /etc/hosts), then re-run:\n    {cmd}",
        needs_hosts=True,
        command=cmd,
    )


def _start_xiaomi_flow(base_url: str, token: str, region: str, language: str) -> dict:
    """Start the xiaomi_home config flow through eula + auth_config; return the
    step dict that follows (expected to carry the OAuth login URL)."""
    code, flow = _http(
        "POST",
        f"{base_url}/api/config/config_entries/flow",
        token=token,
        json_body={"handler": "xiaomi_home"},
    )
    if code not in (200, 201) or not isinstance(flow, dict):
        fail(f"Could not start xiaomi_home config flow (HTTP {code}): {flow}")
    fid = flow["flow_id"]

    # Step 1: EULA
    _http("POST", f"{base_url}/api/config/config_entries/flow/{fid}", token=token, json_body={"eula": True})
    # Step 2: auth_config — read the current schema to reuse the single fixed redirect option.
    code, step = _http("GET", f"{base_url}/api/config/config_entries/flow/{fid}", token=token)
    redirect = XIAOMI_OAUTH_HOST  # fallback
    if isinstance(step, dict):
        for f in step.get("data_schema", []):
            if f.get("name") == "oauth_redirect_url":
                opts = f.get("options") or []
                if opts:
                    redirect = opts[0][0]
    code, nxt = _http(
        "POST",
        f"{base_url}/api/config/config_entries/flow/{fid}",
        token=token,
        json_body={
            "cloud_server": region,
            "integration_language": language,
            "oauth_redirect_url": redirect,
            "network_detect_config": False,
        },
    )
    if not isinstance(nxt, dict):
        fail(f"auth_config step failed: {nxt}")
    nxt["flow_id"] = fid
    return nxt


def _extract_oauth_url(step: dict) -> Optional[str]:
    """Pull the Xiaomi login URL out of a config-flow step.

    ha_xiaomi_home uses a `show_progress` step whose `description_placeholders`
    carry the URL inside an HTML anchor (`link_left = '<a href="https://…">'`),
    so we regex a URL out of any placeholder value. Also handles the simpler
    `external` step that exposes `url` directly.
    """
    if step.get("url"):
        return step["url"]
    ph = step.get("description_placeholders") or {}
    for v in ph.values():
        if not isinstance(v, str):
            continue
        m = re.search(r"https?://[^\s\"'<>]+", v)
        if m:
            return m.group(0)
    return None


def cmd_xiaomi_login(args: argparse.Namespace) -> None:
    """Start the Xiaomi config flow and emit the OAuth login URL for the user."""
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{DEFAULT_PORT}")
    token = st.get("llat")
    if not token:
        fail("No token in state. Run `init` first.")

    log("Starting Xiaomi config flow…")
    step = _start_xiaomi_flow(base_url, token, args.region, args.language)
    st["flow_id"] = step["flow_id"]
    save_state(st)

    url = _extract_oauth_url(step)
    if not url:
        # Save the raw step so we can inspect if the integration version differs.
        fail(
            "Started the flow but could not find the OAuth URL in the step. Raw step saved to state for inspection.",
            step_type=step.get("type"),
            step_id=step.get("step_id"),
        )
        return
    st["oauth_url"] = url
    save_state(st)

    # Two ways to complete, depending on whether homeassistant.local resolves:
    if _hosts_resolves():
        follow_up = "the browser will land back on Home Assistant. Then run `xiaomi-finish`."
    else:
        follow_up = (
            "the browser will fail to open a `homeassistant.local` page — that is expected. "
            "In that failed page's address bar, find the `code=` value (looks like `C3_...`, "
            "ends before the `&`) and run:\n"
            "        xiaomi-callback --code <that code value>\n"
            "(Just the code — no full URL, no quoting. The rest is recovered automatically.)"
        )
    action_required(
        f"Open this link, sign into your Xiaomi account and approve access. After that, {follow_up}\n    {url}",
        oauth_url=url,
        flow_id=step["flow_id"],
        hosts_ok=_hosts_resolves(),
    )


def _submit_form_defaults(flow_url: str, token: str, step: dict) -> Any:
    """Submit a config-flow form using schema defaults + all options for
    multi-selects (e.g. "pick which homes/devices to import" → import all).
    Returns the next step dict."""
    payload: dict[str, Any] = {}
    for field in step.get("data_schema", []):
        name = field.get("name")
        if not name:
            continue
        if "default" in field:
            payload[name] = field["default"]
        elif field.get("type") == "multi_select" and field.get("options"):
            opts = field["options"]
            # options may be [[value,label],…] or {value:label}
            if isinstance(opts, dict):
                payload[name] = list(opts.keys())
            else:
                payload[name] = [o[0] if isinstance(o, (list, tuple)) else o for o in opts]
    _, nxt = _http("POST", flow_url, token=token, json_body=payload)
    return nxt


def _complete_flow(base_url: str, token: str, fid: str) -> Any:
    """Poll a pending config flow to completion.

    Handles the show_progress wait (user authorizing) and any selection forms
    (auto-filled via _submit_form_defaults). Returns the final step dict.
    """
    flow_url = f"{base_url}/api/config/config_entries/flow/{fid}"
    step: Any = None
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        _, step = _http("GET", flow_url, token=token)
        if not isinstance(step, dict):
            break
        stype = step.get("type")
        if stype == "create_entry":
            break
        if stype == "abort":
            fail(f"Flow aborted: {step.get('reason')}", step=step)
        if stype in ("progress", "show_progress"):
            time.sleep(3)  # waiting on the OAuth callback
            continue
        if stype == "form":
            step = _submit_form_defaults(flow_url, token, step)
            if isinstance(step, dict) and step.get("type") == "create_entry":
                break
            continue
        time.sleep(2)
    return step


def _entry_state(base_url: str, token: str, domain: str) -> Any:
    """Return (state, reason) of the first config entry for a domain, or (None, None).

    Uses the entry state to tell "flow created an entry" apart from "the
    integration actually set up and loaded devices" — they are NOT the same: an
    entry can be created and then fail setup (e.g. a transient cloud error),
    leaving zero devices. Best-effort; returns (None, None) if unavailable.
    """
    _, entries = _http("GET", f"{base_url}/api/config/config_entries/entry", token=token)
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and e.get("domain") == domain:
                return e.get("state"), e.get("reason")
    return None, None


def _report_connected(st: dict, base_url: str, token: str, step: Any) -> None:
    """Verify create_entry AND that the integration actually loaded, then report."""
    if not isinstance(step, dict) or step.get("type") != "create_entry":
        fail(
            "Flow did not complete. Make sure you finished the Xiaomi login/approval in the browser, then retry.",
            last_step=step if isinstance(step, dict) else None,
        )
    st.pop("flow_id", None)
    st.pop("oauth_url", None)
    save_state(st)

    # A created entry is not a loaded entry — verify setup actually succeeded.
    state, reason = _entry_state(base_url, token, "xiaomi_home")
    if state is not None and state != "loaded":
        fail(
            f"Config entry was created but the integration failed to set up "
            f"(state={state}, reason={reason}). Often a transient cloud-connectivity "
            f"error. Run `reset-xiaomi` then retry `xiaomi-login`.",
            entry_state=state,
            entry_reason=reason,
        )

    _, states = _http("GET", f"{base_url}/api/states", token=token)
    n = len(states) if isinstance(states, list) else 0
    log(f"\n✅ Xiaomi Home connected. {n} entities available.")
    emit_result(status="ok", connected=True, entity_count=n, base_url=base_url, token=st.get("llat"))


def _webhook_path_and_state_from_oauth(oauth_url: str) -> tuple[Optional[str], Optional[str]]:
    """Recover (webhook_path, state) from the stored authorize URL.

    The authorize URL carries `redirect_uri=<url-encoded .../api/webhook/<id>>`
    and `state=<token>`, so we can rebuild the callback ourselves and only need
    the `code` from the user — no pasting a raw URL full of shell-hostile `&`.
    """
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(oauth_url).query)
    state = (q.get("state") or [None])[0]
    redirect = (q.get("redirect_uri") or [None])[0]
    path = urllib.parse.urlsplit(redirect).path if redirect else None
    return path, state


def cmd_xiaomi_callback(args: argparse.Namespace) -> None:
    """Complete OAuth WITHOUT touching /etc/hosts (Approach A).

    After the user authorizes, Xiaomi redirects the browser to an unreachable
    `homeassistant.local/api/webhook/<id>?code=…&state=…`. HA routes webhooks by
    path and ignores the Host header, so we replay that callback against the
    reachable base_url to deliver the `code`, then poll the flow to completion.

    Two input forms (prefer --code — it avoids the shell-hostile `&` in a raw URL):
      --code <code> [--state <state>]   # webhook path + state recovered from state file
      <full callback URL>               # positional; MUST be shell-quoted
    """
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{DEFAULT_PORT}")
    token = st.get("llat")
    fid = st.get("flow_id")
    if not token or not fid:
        fail("No pending flow in state. Run `xiaomi-login` first.")

    base = urllib.parse.urlsplit(base_url)

    if args.code:
        # Robust path: rebuild the callback from the stored authorize URL + code.
        path, state = _webhook_path_and_state_from_oauth(st.get("oauth_url", ""))
        state = args.state or state
        if not path or not state:
            fail("Could not recover the webhook path/state from state. Re-run `xiaomi-login`.")
        query = urllib.parse.urlencode({"code": args.code, "state": state})
        delivery = urllib.parse.urlunsplit((base.scheme, base.netloc, path, query, ""))
    elif args.callback_url:
        parts = urllib.parse.urlsplit(args.callback_url)
        if "/api/webhook/" not in parts.path:
            fail(
                "That does not look like the HA OAuth callback URL "
                "(expected …/api/webhook/…?code=…&state=…). If you pasted a URL, quote it; "
                "or just pass --code <the code= value>.",
                given=args.callback_url,
            )
        # Keep the original path+query; force our reachable host.
        delivery = urllib.parse.urlunsplit((base.scheme, base.netloc, parts.path, parts.query, ""))
    else:
        fail("Provide --code <code> (recommended) or the full callback URL (quoted).")
        return

    log(f"Delivering the OAuth callback to {base.netloc} (Host is ignored by HA)…")
    code, _ = _http("GET", delivery, timeout=30)
    if code == 0:
        fail("Could not reach HA to deliver the callback.", delivery=delivery)

    log("Callback delivered. Completing the flow…")
    step = _complete_flow(base_url, token, fid)
    _report_connected(st, base_url, token, step)


def cmd_xiaomi_finish(args: argparse.Namespace) -> None:
    """Finish the flow when the callback already reached HA (hosts path)."""
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{DEFAULT_PORT}")
    token = st.get("llat")
    fid = st.get("flow_id")
    if not token or not fid:
        fail("No pending flow in state. Run `xiaomi-login` first.")

    log("Resuming the Xiaomi config flow…")
    step = _complete_flow(base_url, token, fid)
    _report_connected(st, base_url, token, step)


def cmd_reset_xiaomi(args: argparse.Namespace) -> None:
    """Remove the xiaomi_home config entry + its storage, freeing the account.

    ha_xiaomi_home allows only ONE entry per Xiaomi account (it aborts the flow
    if the unique_id is already configured). So a failed/partial setup must be
    cleared before retrying `xiaomi-login`.
    """
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{DEFAULT_PORT}")
    container = st.get("container", DEFAULT_CONTAINER)
    token = st.get("llat")
    if not token:
        fail("No token in state. Run `init` first.")

    _, entries = _http("GET", f"{base_url}/api/config/config_entries/entry", token=token)
    removed = 0
    if isinstance(entries, list):
        for e in entries:
            if isinstance(e, dict) and e.get("domain") == "xiaomi_home":
                eid = e.get("entry_id")
                code, _ = _http("DELETE", f"{base_url}/api/config/config_entries/entry/{eid}", token=token)
                if code in (200, 204):
                    removed += 1

    # Clear the per-account storage so a fresh flow re-fetches everything.
    if container_state(container):
        _docker(
            "exec",
            container,
            "sh",
            "-c",
            "rm -rf /config/.storage/xiaomi_home/miot_config /config/.storage/xiaomi_home/miot_devices",
        )
    st.pop("flow_id", None)
    st.pop("oauth_url", None)
    save_state(st)
    log(f"Removed {removed} xiaomi_home entry(ies) and cleared its storage. Re-run `xiaomi-login`.")
    emit_result(status="ok", removed=removed)


def cmd_bind(args: argparse.Namespace) -> None:
    """Convenience: write base_url+token into NarraNexus via its backend route."""
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{DEFAULT_PORT}")
    token = st.get("llat")
    if not token:
        fail("No token in state. Run `init` first.")

    req = urllib.request.Request(
        f"{args.backend_url}/api/home-assistant/binding",
        data=json.dumps({"agent_id": args.agent_id, "base_url": base_url, "token": token, "verify_tls": True}).encode(),
        headers={"Content-Type": "application/json", "X-User-Id": args.user_id},
        method="PUT",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            ok = resp.status == 200
    except urllib.error.HTTPError as e:
        fail(f"Bind failed (HTTP {e.code}): {e.read().decode('utf-8', 'replace')}")
        return
    except (urllib.error.URLError, OSError) as e:
        fail(f"Could not reach NarraNexus backend: {e}")
        return
    log("Bound HA into NarraNexus." if ok else "Bind returned non-200.")
    emit_result(status="ok" if ok else "error", bound=ok, agent_id=args.agent_id)


def cmd_doctor(args: argparse.Namespace) -> None:
    """Read-only diagnosis of the whole chain."""
    st = load_state()
    base_url = st.get("base_url", f"http://localhost:{args.port}")
    container = st.get("container", DEFAULT_CONTAINER)

    report: dict[str, Any] = {
        "docker": docker_available(),
        "container": container_state(container),
        "ha_reachable": ha_ready(base_url),
        "base_url": base_url,
    }
    if report["ha_reachable"]:
        steps = _onboarding_steps(base_url)
        report["onboarding"] = steps if steps else "complete"
        if container_state(container):
            chk = _docker(
                "exec",
                container,
                "sh",
                "-c",
                "test -d /config/custom_components/xiaomi_home && echo yes || echo no",
            )
            report["xiaomi_installed"] = chk.stdout.strip() == "yes"
    report["hosts_ok"] = _hosts_resolves()
    report["has_token"] = bool(st.get("llat"))

    log(json.dumps(report, indent=2, ensure_ascii=False))
    emit_result(status="ok", **report)


def cmd_all(args: argparse.Namespace) -> None:
    """Orchestrate the full chain, pausing at the one human step (Approach A).

    No /etc/hosts / sudo: xiaomi-login exits(10) with the login link and tells
    the user to paste the callback URL back for `xiaomi-callback`.

    Note: each sub-command emits its own RESULT line, so this prints several;
    the LAST one (xiaomi-login's action_required) is the authoritative outcome.
    """
    cmd_deploy(args)
    cmd_init(args)
    cmd_install_xiaomi(args)
    cmd_xiaomi_login(args)  # exits(10) with the login link + next-step instruction


# ---------------------------------------------------------------------------
# CLI wiring
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the argparse command tree."""
    p = argparse.ArgumentParser(prog="ha-setup", description="Home Assistant + Xiaomi onboarding CLI.")
    sub = p.add_subparsers(dest="command", required=True)

    def add_port(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--port", type=int, default=DEFAULT_PORT)

    sp = sub.add_parser("deploy", help="Docker-deploy Home Assistant.")
    add_port(sp)
    sp.add_argument("--container", default=DEFAULT_CONTAINER, help="Container name.")
    sp.set_defaults(func=cmd_deploy)

    sp = sub.add_parser("init", help="Create admin + mint Long-Lived token.")
    add_port(sp)
    sp.add_argument("--name", default="Admin", help="Display name.")
    sp.add_argument("--username", default="admin")
    sp.add_argument("--password", default=None)
    sp.add_argument("--gen", action="store_true", help="Generate a strong password.")
    sp.add_argument("--language", default="en")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("install-xiaomi", help="Install ha_xiaomi_home integration.")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_install_xiaomi)

    sp = sub.add_parser("hosts", help="Ensure homeassistant.local resolves locally.")
    sp.set_defaults(func=cmd_hosts)

    sp = sub.add_parser("xiaomi-login", help="Start Xiaomi flow, emit OAuth URL.")
    sp.add_argument("--region", default="cn", help="cloud_server: cn/de/i2/ru/sg/us")
    sp.add_argument("--language", default="zh-Hans")
    sp.set_defaults(func=cmd_xiaomi_login)

    sp = sub.add_parser("xiaomi-finish", help="Finish Xiaomi flow after login.")
    sp.set_defaults(func=cmd_xiaomi_finish)

    sp = sub.add_parser(
        "xiaomi-callback",
        help="Complete OAuth without /etc/hosts: replay the pasted callback URL.",
    )
    sp.add_argument(
        "callback_url",
        nargs="?",
        default=None,
        help="Full callback URL from the address bar (MUST be quoted). Prefer --code instead.",
    )
    sp.add_argument("--code", default=None, help="Just the code= value from the callback URL (no quoting needed).")
    sp.add_argument("--state", default=None, help="Optional; recovered from state file if omitted.")
    sp.set_defaults(func=cmd_xiaomi_callback)

    sp = sub.add_parser("reset-xiaomi", help="Remove the xiaomi_home entry + storage to retry.")
    sp.set_defaults(func=cmd_reset_xiaomi)

    sp = sub.add_parser("bind", help="Write binding into NarraNexus.")
    sp.add_argument("--backend-url", default="http://localhost:8000")
    sp.add_argument("--agent-id", required=True)
    sp.add_argument("--user-id", default="local-user")
    sp.set_defaults(func=cmd_bind)

    sp = sub.add_parser("doctor", help="Diagnose the whole chain (read-only).")
    add_port(sp)
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("all", help="Run the full chain, pausing for human steps.")
    add_port(sp)
    sp.add_argument("--container", default=DEFAULT_CONTAINER, help="Container name.")
    sp.add_argument("--name", default="Admin", help="Admin display name.")
    sp.add_argument("--username", default="admin")
    sp.add_argument("--password", default=None)
    sp.add_argument("--gen", action="store_true")
    sp.add_argument("--language", default="en")
    sp.add_argument("--region", default="cn")
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_all)

    return p


def main() -> None:
    """Entry point."""
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
