"""
@file_name: providers.py
@author: NexusAgent
@date: 2026-04-08
@description: REST API routes for LLM provider and slot configuration

Per-user provider isolation: each user has their own providers and slots
stored in user_providers and user_slots tables. Works identically on
both SQLite (local) and MySQL (cloud).
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from xyz_agent_context.agent_framework.model_catalog import (
    get_all_known_models,
    get_default_models,
    get_suggested_models,
    OFFICIAL_BASE_URLS,
)
from xyz_agent_context.schema.provider_schema import (
    LLMConfig,
    SlotName,
    SLOT_REQUIRED_PROTOCOLS,
)

router = APIRouter()


# =============================================================================
# Request/Response Models
# =============================================================================

class SlotDefault(BaseModel):
    protocol: str
    model: str


class AddProviderRequest(BaseModel):
    card_type: str
    name: str = ""
    api_key: str = ""
    base_url: str = ""
    auth_type: str = "api_key"
    models: list[str] = []
    default_slots: dict[str, SlotDefault] | None = None


class SetSlotRequest(BaseModel):
    provider_id: str
    model: str
    # Framework-neutral reasoning params (see provider_schema.SlotConfig).
    # "" = auto. PUT semantics: the UI always sends the current values;
    # omitted fields reset to auto.
    thinking: str = ""
    reasoning_effort: str = ""


class OnboardRequest(BaseModel):
    """Body for ``POST /api/providers/onboard`` — the one-key setup path."""
    api_key: str
    # "anthropic" | "openai" | "netmind"; None → auto-detect from the
    # key prefix (sk-ant- → anthropic, anything else → openai).
    # netmind is only reachable explicitly — its keys have no
    # recognisable prefix.
    provider_type: str | None = None


class UpdateModelsRequest(BaseModel):
    models: list[str]


class SetAgentFrameworkRequest(BaseModel):
    """Body for ``POST /api/providers/agent-framework``."""
    framework: str  # "claude_code" | "codex_cli"


# =============================================================================
# Helpers
# =============================================================================

def _get_user_id(request: Request) -> str:
    """Return the identity established by auth_middleware.

    Cloud mode: JWT-decoded user_id; local mode: X-User-Id header.
    Either way, middleware writes ``request.state.user_id`` BEFORE the
    handler runs, and 401s the request if the caller didn't declare an
    identity — so by the time we get here, the value is present and is
    the only legitimate source.

    The previous version also accepted a ``user_id`` query param as a
    backup. That was a cross-user write/read bug waiting to happen: the
    frontend could trivially construct ``?user_id=alice`` while logged
    in as bob, and have the backend silently scope provider config to
    alice. Identity must come from auth_middleware, never the URL.
    """
    uid = getattr(request.state, "user_id", None)
    if not uid:
        raise HTTPException(
            status_code=401,
            detail="Authentication required (no user_id on request.state)",
        )
    return uid


# Card types that authenticate via a SHARED CLI credential file
# (~/.claude/.credentials.json, ~/.codex/auth.json) staged from a single
# staff `claude login` / `codex login`. The cloud image runs one `app`
# user with one HOME, so those files are container-global. A non-staff
# cloud user wiring such a card — or switching the agent framework to one
# that resolves to them — would ride staff's credentials (consume their
# quota, act under their identity). API-key cards carry the user's own
# key and never touch the shared files, so they stay open.
_OAUTH_CARD_TYPES = frozenset({"claude_oauth", "codex_oauth"})


def _is_cloud() -> bool:
    """Cloud deployment runs on a non-sqlite backend (MySQL)."""
    return not os.environ.get("DATABASE_URL", "").startswith("sqlite")


def _is_staff(request: Request) -> bool:
    """Staff role, injected into request.state by auth_middleware."""
    return getattr(request.state, "role", "") == "staff"


async def _get_service():
    """Get UserProviderService with DB client."""
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.agent_framework.user_provider_service import UserProviderService
    db = await get_db_client()
    return UserProviderService(db)


def _config_to_response(config: LLMConfig) -> dict:
    """Convert LLMConfig to API response dict with masked api_key."""
    providers = {}
    for pid, prov in config.providers.items():
        d = prov.model_dump(mode="json")
        if d["api_key"] and len(d["api_key"]) > 4:
            d["api_key_masked"] = "***" + d["api_key"][-4:]
        else:
            d["api_key_masked"] = "***"
        del d["api_key"]
        providers[pid] = d

    slots = {}
    for slot_name in SlotName:
        slot_str = slot_name.value
        required = [p.value for p in SLOT_REQUIRED_PROTOCOLS.get(slot_str, [])]
        slot_cfg = config.slots.get(slot_str)
        slots[slot_str] = {
            "required_protocols": required,
            "config": slot_cfg.model_dump() if slot_cfg else None,
        }

    return {"version": config.version, "providers": providers, "slots": slots}


async def _run_json_subprocess(args: list[str], timeout: float) -> dict | None:
    """Run a short CLI probe without blocking the FastAPI event loop."""
    import asyncio
    import json as _json
    from contextlib import suppress

    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        return None

    try:
        stdout, _stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        with suppress(ProcessLookupError, OSError):
            proc.kill()
        with suppress(Exception):
            await proc.wait()
        return None

    if proc.returncode != 0 or not stdout.strip():
        return None

    try:
        data = _json.loads(stdout.decode("utf-8", errors="replace"))
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


# =============================================================================
# Endpoints
# =============================================================================

@router.get("")
async def get_providers(request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    config = await service.get_user_config(uid)
    return {"success": True, "data": _config_to_response(config)}


@router.post("")
async def add_provider(req: AddProviderRequest, request: Request):
    uid = _get_user_id(request)
    if req.card_type in _OAUTH_CARD_TYPES and _is_cloud() and not _is_staff(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "OAuth provider cards (Claude / Codex CLI login) are "
                "staff-only in cloud mode — they ride the shared CLI "
                "credentials. Add an API-key provider instead."
            ),
        )
    try:
        service = await _get_service()
        config, new_ids = await service.add_provider(
            user_id=uid,
            card_type=req.card_type,
            name=req.name,
            api_key=req.api_key,
            base_url=req.base_url,
            auth_type=req.auth_type,
            models=req.models if req.models else None,
        )

        if req.default_slots:
            for slot_name, slot_def in req.default_slots.items():
                match_pid = None
                for pid in new_ids:
                    prov = config.providers.get(pid)
                    if prov and prov.protocol.value == slot_def.protocol:
                        match_pid = pid
                        break
                if match_pid:
                    config = await service.set_slot(uid, slot_name, match_pid, slot_def.model)

        # Hot-reload for current process (local mode)
        try:
            from xyz_agent_context.agent_framework.api_config import (
                get_user_runtime_llm_configs,
                set_user_config,
            )
            cfg = await get_user_runtime_llm_configs(uid)
            set_user_config(cfg.claude, cfg.openai, cfg.codex, cfg.anthropic_helper)
        except Exception:
            pass

        # Edge-triggered recovery: a newly-added provider (with default slots)
        # can make the user runnable — revive their PAUSED_NO_QUOTA jobs.
        from xyz_agent_context.module.job_module.job_recovery import (
            schedule_user_no_quota_rearm,
        )
        schedule_user_no_quota_rearm(uid)

        return {"success": True, "provider_ids": new_ids, "data": _config_to_response(config)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"[add_provider] Error: {e}", exc_info=True)
        return {"success": False, "detail": str(e)}


@router.post("/onboard")
async def onboard(req: OnboardRequest, request: Request):
    """One-key setup: a single API key wires everything needed to chat.

    All orchestration (key-type detection, framework persistence,
    provider creation, both slot assignments) lives in
    ``UserProviderService.onboard_one_key``; this route only adds the
    HTTP envelope, hot-reload, and job recovery.
    """
    uid = _get_user_id(request)
    try:
        service = await _get_service()
        config, new_ids, meta = await service.onboard_one_key(
            uid, req.api_key, req.provider_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Hot-reload for current process (mirror add_provider / set_slot)
    try:
        from xyz_agent_context.agent_framework.api_config import (
            get_user_runtime_llm_configs,
            set_user_config,
        )
        cfg = await get_user_runtime_llm_configs(uid)
        set_user_config(cfg.claude, cfg.openai, cfg.codex, cfg.anthropic_helper)
    except Exception:
        pass

    # Edge-triggered recovery: the user just became runnable.
    from xyz_agent_context.module.job_module.job_recovery import (
        schedule_user_no_quota_rearm,
    )
    schedule_user_no_quota_rearm(uid)

    return {
        "success": True,
        "provider_ids": new_ids,
        **meta,
        "data": _config_to_response(config),
    }


@router.delete("/{provider_id}")
async def remove_provider(provider_id: str, request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    try:
        config = await service.remove_provider(uid, provider_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": _config_to_response(config)}


@router.post("/{provider_id}/test")
async def test_provider(provider_id: str, request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    success, message = await service.test_provider(uid, provider_id)
    return {"success": success, "message": message}


@router.post("/sync-defaults")
async def sync_default_models(request: Request):
    """Refresh this user's provider model lists.

    For auto-discovered aggregators (netmind / openrouter / yunwu) this runs the
    same probe logic as the cloud daily job ([[model_sync]]): fetch the catalog,
    probe new (and previously-failed) models with the user's own key, then
    OVERWRITE each provider's model list with what actually answers on its
    protocol. Dedup means only new/failed models are probed, so a click is
    usually instant (the shipped ledger already covers the known models).

    Out-of-scope sources (claude_oauth / codex_oauth) keep the catalog defaults;
    `source="user"` (hand-picked custom providers) is left untouched.
    """
    from xyz_agent_context.agent_framework import model_sync

    uid = _get_user_id(request)
    service = await _get_service()
    config = await service.get_user_config(uid)

    # Group this user's provider rows by source so we probe each backend once.
    by_source: dict[str, list] = {}
    for prov_id, prov in config.providers.items():
        by_source.setdefault(prov.source.value, []).append((prov_id, prov))

    updates: list[dict] = []

    async def _apply(prov_id, prov, new_models: list[str]):
        existing = list(prov.models or [])
        if set(new_models) == set(existing):
            return
        await service.update_models(uid, prov_id, new_models)
        updates.append({
            "provider_id": prov_id,
            "name": prov.name,
            "source": prov.source.value,
            "protocol": prov.protocol.value,
            "added": [m for m in new_models if m not in existing],
            "removed": [m for m in existing if m not in new_models],
        })

    for source, rows in by_source.items():
        if source == "user":
            continue  # hand-picked custom provider — never auto-touch

        if source in model_sync.SUPPORTED_SOURCES and source != "system_pool":
            # Probe with the user's own key (same key works for both protocols
            # on these aggregators). OVERWRITE each row from the result.
            anykey = next((p.api_key for _, p in rows if p.api_key), "")
            keys = {"openai": anykey, "anthropic": anykey}
            yunwu_key = anykey if source == "yunwu" else None
            try:
                res = await model_sync.sync_source(source, keys=keys, yunwu_key=yunwu_key)
            except Exception as e:  # noqa: BLE001 — catalog/probe failure shouldn't 500 the button
                logger.warning(f"sync-defaults: {source} probe failed: {e}")
                continue
            for prov_id, prov in rows:
                await _apply(prov_id, prov, list(res.lists.get(prov.protocol.value, [])))
        else:
            # Out-of-scope source: append any new catalog defaults (legacy behavior).
            for prov_id, prov in rows:
                defaults = list(get_default_models(source, prov.protocol.value))
                if not defaults:
                    continue
                existing = list(prov.models or [])
                missing = [m for m in defaults if m not in existing]
                if missing:
                    await _apply(prov_id, prov, existing + missing)

    return {
        "success": True,
        "updates": updates,
        "providers_updated": len(updates),
        "total_models_added": sum(len(u["added"]) for u in updates),
    }


@router.put("/{provider_id}/models")
async def update_provider_models(provider_id: str, req: UpdateModelsRequest, request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    try:
        config = await service.update_models(uid, provider_id, req.models)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return {"success": True, "data": _config_to_response(config)}


@router.put("/slots/{slot_name}")
async def set_slot(slot_name: str, req: SetSlotRequest, request: Request):
    uid = _get_user_id(request)
    try:
        service = await _get_service()
        config = await service.set_slot(
            uid, slot_name, req.provider_id, req.model,
            thinking=req.thinking, reasoning_effort=req.reasoning_effort,
        )

        errors = []
        for s in SlotName:
            if s.value not in config.slots:
                errors.append(f"Slot '{s.value}' not configured")

        # Hot-reload for current process
        try:
            from xyz_agent_context.agent_framework.api_config import (
                get_user_runtime_llm_configs,
                set_user_config,
            )
            cfg = await get_user_runtime_llm_configs(uid)
            set_user_config(cfg.claude, cfg.openai, cfg.codex, cfg.anthropic_helper)
        except Exception:
            pass

        # Edge-triggered recovery: completing/changing the agent slot can make
        # the user runnable — revive their PAUSED_NO_QUOTA jobs (non-blocking).
        from xyz_agent_context.module.job_module.job_recovery import (
            schedule_user_no_quota_rearm,
        )
        schedule_user_no_quota_rearm(uid)

        return {"success": True, "data": _config_to_response(config), "validation_errors": errors}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/slots/validate")
async def validate_slots(request: Request):
    uid = _get_user_id(request)
    service = await _get_service()
    errors = await service.validate_slots(uid)
    return {"success": True, "errors": errors, "all_configured": len(errors) == 0}


# =============================================================================
# Agent Framework (coding-agent CLI choice — Claude Code vs Codex CLI)
# =============================================================================
#
# Persisted as ``user_slots[user_id, slot_name='agent'].agent_framework``.
# Read by ``step_3_agent_loop._resolve_agent_framework_sdk`` per turn
# to pick which SDK class drives the agent_loop. Defaults to
# "claude_code" so existing users are unaffected.


from xyz_agent_context.agent_framework.user_provider_service import (
    UserProviderService as _UserProviderServiceForFrameworks,
)
# Single source of truth — keep the route's whitelist in sync with the
# service layer. Adding a v3 framework name in user_provider_service
# automatically opens the route here, no double-edit required.
_SUPPORTED_AGENT_FRAMEWORKS = _UserProviderServiceForFrameworks._SUPPORTED_AGENT_FRAMEWORKS

async def _ensure_codex_installed() -> dict:
    """Verify the codex binary bundled with the ``openai-codex-cli-bin``
    wheel is available. No PATH check, no npm install — both became
    obsolete when v2 cutover made ``openai-codex`` (which transitively
    ships ``openai-codex-cli-bin``) a hard dependency in pyproject.toml.

    Behaviour:
      - ``uv sync`` (run by ``run.sh`` and during DMG build) installs
        both wheels. The binary lands at
        ``.venv/lib/python3.X/site-packages/codex_cli_bin/bin/codex`` —
        NOT on PATH, but the openai-codex SDK calls
        ``bundled_codex_path()`` to locate it directly, so PATH is
        irrelevant.
      - If the wheel is missing, the user's deploy is broken upstream
        (uv sync failed); we report a clear actionable error so they
        re-run install rather than seeing an inscrutable
        ``codex: command not found`` at agent_loop time.

    Pre-2026-06-08 history (kept for context): this function used to
    ``npm install -g @openai/codex`` lazily on framework switch. That
    path was correct for v1 (which spawned ``codex exec`` from PATH)
    but is now dead code — v2's SDK uses the wheel-bundled binary.
    Worse, on the DMG build the npm path always failed (no npm in the
    bundled environment) and surfaced a misleading red banner even
    though codex actually worked. binding rule #7 (DMG + bash run.sh
    must align) made this a hard fix, not optional.

    Returns:
        Dict shape ``{"installed": bool, "action": str, "reason": str}``.
        ``action`` is always ``"already_installed"`` on success or
        ``"install_failed"`` with an actionable reason on failure.
        ``"auto_installed"`` / ``"blocked"`` no longer fire — both
        were states the npm path produced.
    """
    try:
        from codex_cli_bin import bundled_codex_path  # noqa: PLC0415
    except ImportError as e:
        return {
            "installed": False,
            "action": "install_failed",
            "reason": (
                f"openai-codex-cli-bin wheel not importable ({e}). "
                f"Run ``uv sync`` to install the openai-codex SDK "
                f"and its bundled binary wheel."
            ),
        }

    binary = bundled_codex_path()
    if not binary.exists():
        return {
            "installed": False,
            "action": "install_failed",
            "reason": (
                f"codex_cli_bin imported but bundled binary missing at "
                f"{binary}. Re-run ``uv sync`` to repair the install."
            ),
        }

    return {
        "installed": True,
        "action": "already_installed",
        "reason": "",
    }


async def _probe_agent_framework_auth(framework: str, user_id: str | None = None) -> dict:
    """Probe whether the framework can authenticate for this user.

    Returns ``{"ok": bool, "detail": str}``. TWO legitimate auth legs:

    1. **API-key provider** — the user's agent slot is wired to a
       provider with a real api_key matching the framework's protocol
       (the one-key onboarding path). No CLI login needed; checked
       first when ``user_id`` is given.
    2. **CLI OAuth** — ``codex login`` / ``claude`` credentials file on
       the host, probed via a stub ProviderCard + the OAuth driver.

    The previous version only checked leg 2, which falsely reported
    "auth missing" for perfectly runnable API-key users.
    """
    # ── Leg 1: API-key provider on the agent slot ─────────────────────
    if user_id:
        required_proto = "openai" if framework == "codex_cli" else "anthropic"
        try:
            from xyz_agent_context.utils.db_factory import get_db_client
            db = await get_db_client()
            slot = await db.get_one(
                "user_slots", {"user_id": user_id, "slot_name": "agent"}
            )
            if slot and slot.get("provider_id"):
                prov = await db.get_one(
                    "user_providers", {"provider_id": slot["provider_id"]}
                )
                if (
                    prov
                    and prov.get("api_key")
                    and (prov.get("protocol") or "").lower() == required_proto
                ):
                    return {
                        "ok": True,
                        "detail": (
                            f"API-key provider configured "
                            f"({prov.get('name') or slot['provider_id']})"
                        ),
                    }
        except Exception as e:  # noqa: BLE001 — fall through to OAuth probe
            logger.warning(
                f"[agent-framework probe] api-key leg failed for "
                f"user={user_id!r}: {e}"
            )

    # ── Leg 2: CLI OAuth credentials on the host ─────────────────────
    from xyz_agent_context.agent_framework.provider_driver.base import ProviderCard

    # Codex auth probe — reads ``~/.codex/auth.json`` regardless of
    # which codex driver class is registered (v1 or v2 share the
    # auth file path).
    if framework == "codex_cli":
        from xyz_agent_context.agent_framework.provider_driver.drivers.codex_oauth import (
            CodexOAuthDriver,
        )
        from xyz_agent_context.agent_framework.provider_driver.derive import (
            CODEX_CLI_CREDENTIALS_REF,
        )
        stub = ProviderCard(
            provider_id="_probe_codex",
            user_id="_probe",
            name="probe",
            source="codex_oauth",
            protocol="openai",
            auth_type="oauth",
            api_key="",
            base_url="",
            auth_ref=CODEX_CLI_CREDENTIALS_REF,
            driver_type="codex_oauth",
        )
        health = await CodexOAuthDriver(stub).probe()
        detail = health.detail
        if not health.ok:
            detail += (
                " — or add an API-key OpenAI provider (Custom OpenAI) and "
                "assign it to the agent slot; no codex login needed then."
            )
        return {"ok": health.ok, "detail": detail}

    if framework == "claude_code":
        from xyz_agent_context.agent_framework.provider_driver.drivers.claude_oauth import (
            ClaudeOAuthDriver,
        )
        from xyz_agent_context.agent_framework.provider_driver.derive import (
            CLAUDE_CLI_CREDENTIALS_REF,
        )
        stub = ProviderCard(
            provider_id="_probe_claude",
            user_id="_probe",
            name="probe",
            source="claude_oauth",
            protocol="anthropic",
            auth_type="oauth",
            api_key="",
            base_url="",
            auth_ref=CLAUDE_CLI_CREDENTIALS_REF,
            driver_type="claude_oauth",
        )
        health = await ClaudeOAuthDriver(stub).probe()
        detail = health.detail
        if not health.ok:
            detail += (
                " — or add an API-key Anthropic provider and assign it to "
                "the agent slot; no claude login needed then."
            )
        return {"ok": health.ok, "detail": detail}

    return {"ok": False, "detail": f"unknown framework: {framework}"}


@router.get("/agent-framework")
async def get_agent_framework(request: Request):
    """Return the user's current coding-agent framework + auth probe."""
    uid = _get_user_id(request)
    service = await _get_service()
    framework = await service.get_user_agent_framework(uid)
    probe = await _probe_agent_framework_auth(framework, user_id=uid)
    return {
        "success": True,
        "data": {
            "framework": framework,
            "supported": list(_SUPPORTED_AGENT_FRAMEWORKS),
            "probe": probe,
        },
    }


@router.post("/agent-framework")
async def set_agent_framework(request: Request, body: SetAgentFrameworkRequest):
    """Persist the user's coding-agent framework choice.

    Cloud: staff-only — the gate above 403s non-staff (switching to a
    framework with no API-key provider would fall back to the shared CLI
    credentials).

    Side effect: when ``framework == "codex_cli"`` this verifies the
    codex binary bundled with the ``openai-codex-cli-bin`` wheel is
    available (``_ensure_codex_installed``) and returns the result in
    ``data.install`` so the frontend can surface a clear error if the
    wheel is missing (deploy ran without ``uv sync``). There is NO npm
    install — codex ships as a wheel since the v2 cutover, so ``action``
    is ``already_installed`` or ``install_failed`` (never the old
    ``auto_installed`` / ``blocked``). claude_code skips this — the
    ``claude`` binary is installed at run.sh boot.
    """
    uid = _get_user_id(request)
    if _is_cloud() and not _is_staff(request):
        raise HTTPException(
            status_code=403,
            detail=(
                "Switching the agent framework is staff-only in cloud mode "
                "— a framework with no API-key provider falls back to the "
                "shared CLI credentials. Use one-key onboarding with your "
                "own API key instead."
            ),
        )
    if body.framework not in _SUPPORTED_AGENT_FRAMEWORKS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown framework {body.framework!r}. "
                f"Supported: {list(_SUPPORTED_AGENT_FRAMEWORKS)}"
            ),
        )

    # Verify the wheel-bundled codex binary is present on opt-in. The
    # ``openai-codex`` SDK spawns it in app-server mode, so a missing
    # wheel must surface here, not at agent_loop time. No npm install —
    # see _ensure_codex_installed. claude_code skips this (the `claude`
    # binary is already installed at run.sh boot time).
    install_result: dict | None = None
    if body.framework == "codex_cli":
        install_result = await _ensure_codex_installed()

    service = await _get_service()
    try:
        await service.set_user_agent_framework(uid, body.framework)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    probe = await _probe_agent_framework_auth(body.framework, user_id=uid)
    return {
        "success": True,
        "data": {
            "framework": body.framework,
            "probe": probe,
            "install": install_result,  # null for claude_code, dict for codex_cli
        },
    }


@router.get("/catalog")
async def get_catalog():
    return {
        "success": True,
        "known_models": get_all_known_models(),
        "suggestions": {
            "anthropic": get_suggested_models("anthropic"),
            "openai": get_suggested_models("openai"),
        },
        "official_base_urls": {protocol: list(urls) for protocol, urls in OFFICIAL_BASE_URLS.items()},
        "slot_protocols": {slot.value: [p.value for p in protos] for slot, protos in SLOT_REQUIRED_PROTOCOLS.items()},
    }


# =============================================================================
# Claude Code Auth Status
# =============================================================================

@router.get("/claude-status")
async def get_claude_status(request: Request):
    """Check if Claude Code CLI is logged in. Cloud: only staff can use it.

    Response fields:
      - cli_installed: bool — `claude` binary on PATH
      - logged_in:     bool — auth status reports an active token
      - email:         str | None — account email if discoverable
      - expires_at:    str | None — ISO-8601 token expiry if surfaced
    """
    import json as _json
    from pathlib import Path

    result = {"cli_installed": False, "logged_in": False, "email": None, "expires_at": None}

    if _is_cloud() and not _is_staff(request):
        return {"success": True, "data": {**result, "allowed": False}}

    import shutil
    claude_path = shutil.which("claude")
    if claude_path:
        result["cli_installed"] = True

    # Preferred: use `claude auth status` (Claude Code v2.x+).
    # Output schema isn't formally documented and shifts between minor
    # versions, so we probe a few common shapes for email / expiry instead
    # of pinning to one. Anything we can't parse stays None — the UI just
    # won't show those subfields.
    if claude_path:
        auth_data = await _run_json_subprocess(
            [claude_path, "auth", "status"],
            timeout=10,
        )
        if auth_data:
            if auth_data.get("loggedIn"):
                result["logged_in"] = True
            # Email — try flat then nested under account/user.
            email = auth_data.get("email")
            if not email:
                for nested_key in ("account", "user", "profile"):
                    nested = auth_data.get(nested_key)
                    if isinstance(nested, dict) and nested.get("email"):
                        email = nested["email"]
                        break
            if isinstance(email, str) and email:
                result["email"] = email
            # Expiry — flat fields first, then under token/oauth.
            for key in ("expiresAt", "expires_at", "tokenExpiresAt"):
                val = auth_data.get(key)
                if val:
                    result["expires_at"] = str(val)
                    break
            if not result["expires_at"]:
                for nested_key in ("token", "oauth", "credentials"):
                    nested = auth_data.get(nested_key)
                    if isinstance(nested, dict):
                        for key in ("expiresAt", "expires_at"):
                            if nested.get(key):
                                result["expires_at"] = str(nested[key])
                                break
                        if result["expires_at"]:
                            break

    # Fallback: check legacy credentials file (Claude Code v1.x).
    # Mostly used to backfill logged_in when `claude auth status` is missing
    # or the user is on an older CLI. Email/expires_at usually aren't in
    # this file, so they may stay None even when we mark logged_in=True.
    if not result["logged_in"]:
        creds_file = Path.home() / ".claude" / ".credentials.json"
        if creds_file.is_file():
            try:
                data = _json.loads(creds_file.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    for key in ("accessToken", "oauthToken", "claudeAiOauth", "oauth"):
                        if data.get(key):
                            result["logged_in"] = True
                            break
                    # Best-effort metadata extraction from the credentials
                    # file. Different CLI versions stash these in different
                    # places; we walk the common ones.
                    if not result["email"]:
                        for nested_key in ("claudeAiOauth", "oauth", "account", "user"):
                            nested = data.get(nested_key)
                            if isinstance(nested, dict) and nested.get("email"):
                                result["email"] = nested["email"]
                                break
                    if not result["expires_at"]:
                        for nested_key in ("claudeAiOauth", "oauth"):
                            nested = data.get(nested_key)
                            if isinstance(nested, dict):
                                for key in ("expiresAt", "expires_at"):
                                    if nested.get(key):
                                        result["expires_at"] = str(nested[key])
                                        break
                                if result["expires_at"]:
                                    break
            except Exception:
                pass

    return {"success": True, "data": result}


# =============================================================================
# Codex CLI Auth Status (mirror of /claude-status)
# =============================================================================


def _expiry_is_past(raw: object) -> bool:
    """Best-effort: is this codex auth-token expiry in the past?

    Returns False whenever we can't CONFIDENTLY parse the value — fail
    open, because wrongly reporting a working session as expired is worse
    than under-warning. Handles epoch seconds, epoch milliseconds, and
    ISO-8601 strings (the codex auth.json schema is undocumented and
    varies between versions).
    """
    from datetime import datetime, timezone

    try:
        is_numeric_str = (
            isinstance(raw, str) and raw.strip().lstrip("-").isdigit()
        )
        if isinstance(raw, (int, float)) or is_numeric_str:
            ts = float(raw)  # type: ignore[arg-type]
            if ts > 1e11:  # milliseconds, not seconds
                ts /= 1000.0
            return ts < datetime.now(timezone.utc).timestamp()
        if isinstance(raw, str):
            dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt < datetime.now(timezone.utc)
    except Exception:
        return False
    return False


@router.get("/codex-status")
async def get_codex_status(request: Request):
    """Check if Codex CLI is installed + has an active OAuth session.

    Response fields mirror ``/claude-status`` so the frontend can
    reuse the same UI shape:
      - ``cli_installed``: bool — ``codex`` binary on PATH
      - ``logged_in``:     bool — ``~/.codex/auth.json`` present
      - ``email``:         str | None — best-effort if parseable
      - ``expires_at``:    str | None — best-effort if parseable

    Cloud mode hides the card for non-staff (per /claude-status policy).
    Auth.json's schema is undocumented and may shift between versions;
    we only attempt to extract email + expiry on a best-effort basis,
    leaving them None when we can't parse.
    """
    import json as _json
    from pathlib import Path

    result = {
        "cli_installed": False,
        "logged_in": False,
        "email": None,
        "expires_at": None,
        "expired": False,
    }

    if _is_cloud() and not _is_staff(request):
        return {"success": True, "data": {**result, "allowed": False}}

    import shutil
    if shutil.which("codex"):
        result["cli_installed"] = True

    # Existence check on the auth file is the canonical "logged in"
    # signal — Codex CLI itself uses the same check on subprocess
    # start (it errors with "not logged in" if the file is absent).
    codex_home = os.environ.get("CODEX_HOME")
    if codex_home:
        creds_file = Path(codex_home).expanduser() / "auth.json"
    else:
        creds_file = Path.home() / ".codex" / "auth.json"

    if creds_file.is_file():
        result["logged_in"] = True
        # Best-effort metadata extraction — Codex auth.json schema
        # is undocumented; we look for common shapes and leave the
        # fields None when none match.
        try:
            data = _json.loads(creds_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Email — try flat then nested under common keys
                email = data.get("email")
                if not email:
                    for nested_key in ("account", "user", "profile", "chatgpt"):
                        nested = data.get(nested_key)
                        if isinstance(nested, dict) and nested.get("email"):
                            email = nested["email"]
                            break
                if isinstance(email, str) and email:
                    result["email"] = email
                # Expiry — flat then nested under token/oauth
                for key in ("expiresAt", "expires_at", "tokenExpiresAt"):
                    val = data.get(key)
                    if val:
                        result["expires_at"] = str(val)
                        break
                if not result["expires_at"]:
                    for nested_key in ("token", "oauth", "credentials"):
                        nested = data.get(nested_key)
                        if isinstance(nested, dict):
                            for key in ("expiresAt", "expires_at"):
                                if nested.get(key):
                                    result["expires_at"] = str(nested[key])
                                    break
                            if result["expires_at"]:
                                break
        except Exception:
            # File is present but unparseable — keep logged_in=True
            # (Codex itself would still try to use it), just leave
            # email + expires_at None.
            pass

        # Honesty fix (incident 2026-06-11): a present auth.json is NOT
        # proof the session works — an expired token leaves the file in
        # place, so the old "file exists → logged_in=True" reported a dead
        # session as live and the Settings page showed "✓ auth ready"
        # while every codex turn failed unauthorized. When we can
        # CONFIDENTLY parse the expiry and it's in the past, report
        # logged_in=False so the UI prompts re-login. Fail open otherwise.
        # (The "refresh token already used" case keeps a non-expired access
        # token and can only be caught by a real call — handled at runtime
        # via the auth_expired error path, not here.)
        if result["expires_at"] is not None and _expiry_is_past(result["expires_at"]):
            result["logged_in"] = False
            result["expired"] = True

    return {"success": True, "data": result}


# Embedding migration routes (/embeddings/status, /embeddings/rebuild) removed —
# embeddings are retired (narrative/memory routing is BM25).
