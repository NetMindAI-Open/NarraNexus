#!/usr/bin/env python3
"""
@file_name: configure_providers.py
@author: Bin Liang
@date: 2026-03-24
@description: CLI tool for LLM provider configuration

Bridges run.sh (terminal UI) and provider_registry (core logic).
All provider/slot operations go through the same ProviderRegistry
used by the desktop app and web frontend.

Usage (called by run.sh, not intended for direct user invocation):
    # Show current status (JSON output for run.sh to parse)
    uv run python scripts/configure_providers.py status

    # Add a provider
    uv run python scripts/configure_providers.py add netmind --api-key "xxx"
    uv run python scripts/configure_providers.py add claude_oauth
    uv run python scripts/configure_providers.py add anthropic --api-key "xxx" --base-url "https://..."
    uv run python scripts/configure_providers.py add openai --api-key "xxx"

    # Assign a slot
    uv run python scripts/configure_providers.py assign agent <provider_id> <model>
    uv run python scripts/configure_providers.py assign embedding <provider_id> <model>

    # Check if all slots are configured
    uv run python scripts/configure_providers.py validate

    # Check Claude Code CLI login status
    uv run python scripts/configure_providers.py claude-status
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

# Add project root to path so we can import the package
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from xyz_agent_context.agent_framework.provider_registry import provider_registry
from xyz_agent_context.agent_framework.model_catalog import get_model_display_name


# =============================================================================
# Commands
# =============================================================================

def cmd_status() -> None:
    """Print current provider/slot status as JSON."""
    config = provider_registry.load()

    providers = []
    if config:
        for pid, prov in config.providers.items():
            providers.append({
                "id": pid,
                "name": prov.name,
                "protocol": prov.protocol.value,
                "source": prov.source.value,
                "models": prov.models,
                "key_hint": ("***" + prov.api_key[-4:]) if prov.api_key and len(prov.api_key) > 4 else "(oauth)" if prov.auth_type.value == "oauth" else "***",
            })

    slots = {}
    for slot_name in ("agent", "embedding", "helper_llm"):
        slot_cfg = config.slots.get(slot_name) if config else None
        if slot_cfg and slot_cfg.provider_id:
            prov = config.providers.get(slot_cfg.provider_id) if config else None
            slots[slot_name] = {
                "configured": True,
                "provider_name": prov.name if prov else "?",
                "provider_id": slot_cfg.provider_id,
                "model": slot_cfg.model,
                "model_display": get_model_display_name(slot_cfg.model),
            }
        else:
            slots[slot_name] = {"configured": False}

    print(json.dumps({"providers": providers, "slots": slots}, ensure_ascii=False))


def cmd_add(args: argparse.Namespace) -> None:
    """Add a provider."""
    kwargs: dict = {
        "card_type": args.card_type,
        "api_key": args.api_key or "",
        "base_url": args.base_url or "",
        "name": args.name or "",
        "auth_type": args.auth_type or "api_key",
    }
    if args.models:
        kwargs["models"] = args.models

    config, new_ids = provider_registry.add_provider(**kwargs)

    result = []
    for pid in new_ids:
        prov = config.providers[pid]
        result.append({
            "id": pid,
            "name": prov.name,
            "protocol": prov.protocol.value,
            "models": prov.models,
        })

    print(json.dumps({"success": True, "providers": result}, ensure_ascii=False))


def cmd_assign(args: argparse.Namespace) -> None:
    """Assign a provider + model to a slot."""
    config = provider_registry.load()
    if config is None:
        print(json.dumps({"success": False, "error": "No config found. Add a provider first."}))
        sys.exit(1)

    try:
        config = provider_registry.set_slot(config, args.slot, args.provider_id, args.model)
        provider_registry.save(config)
        print(json.dumps({"success": True, "slot": args.slot, "provider_id": args.provider_id, "model": args.model}))
    except ValueError as e:
        print(json.dumps({"success": False, "error": str(e)}))
        sys.exit(1)


def cmd_validate() -> None:
    """Check if all slots are properly configured."""
    config = provider_registry.load()
    if config is None:
        print(json.dumps({"valid": False, "errors": ["No config found"]}))
        sys.exit(1)

    errors = provider_registry.validate(config)
    print(json.dumps({"valid": len(errors) == 0, "errors": errors}))
    if errors:
        sys.exit(1)


def cmd_claude_status() -> None:
    """Check Claude Code CLI installation and login status."""
    result = {"installed": False, "logged_in": False}

    if shutil.which("claude"):
        result["installed"] = True

    creds_file = Path.home() / ".claude" / ".credentials.json"
    if creds_file.is_file():
        try:
            data = json.loads(creds_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for key in ("accessToken", "oauthToken", "claudeAiOauth"):
                    if data.get(key):
                        result["logged_in"] = True
                        break
                if not result["logged_in"] and data.get("oauth"):
                    result["logged_in"] = True
        except Exception:
            pass

    print(json.dumps(result))


def cmd_list_models(args: argparse.Namespace) -> None:
    """List models available on a provider, optionally filtered by slot type."""
    from xyz_agent_context.agent_framework.model_catalog import get_embedding_dimensions

    config = provider_registry.load()
    if config is None or args.provider_id not in config.providers:
        print(json.dumps({"models": []}))
        return

    prov = config.providers[args.provider_id]
    models = []
    for mid in prov.models:
        is_embedding = get_embedding_dimensions(mid) is not None

        # Filter by slot type if specified
        if args.slot:
            if args.slot == "embedding" and not is_embedding:
                continue
            if args.slot in ("agent", "helper_llm") and is_embedding:
                continue

        models.append({
            "id": mid,
            "display": get_model_display_name(mid),
        })
    print(json.dumps({"models": models}, ensure_ascii=False))


# =============================================================================
# Argument Parser
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="LLM Provider Configuration CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    # status
    sub.add_parser("status", help="Show current provider/slot status")

    # add
    add_p = sub.add_parser("add", help="Add a provider")
    add_p.add_argument("card_type", choices=["netmind", "claude_oauth", "anthropic", "openai"])
    add_p.add_argument("--api-key", default="")
    add_p.add_argument("--base-url", default="")
    add_p.add_argument("--name", default="")
    add_p.add_argument("--auth-type", default="api_key", choices=["api_key", "bearer_token"])
    add_p.add_argument("--models", nargs="*", default=None)

    # assign
    assign_p = sub.add_parser("assign", help="Assign a provider + model to a slot")
    assign_p.add_argument("slot", choices=["agent", "embedding", "helper_llm"])
    assign_p.add_argument("provider_id")
    assign_p.add_argument("model")

    # validate
    sub.add_parser("validate", help="Check if all slots are configured")

    # claude-status
    sub.add_parser("claude-status", help="Check Claude Code CLI status")

    # list-models
    lm_p = sub.add_parser("list-models", help="List models for a provider")
    lm_p.add_argument("provider_id")
    lm_p.add_argument("--slot", default=None, choices=["agent", "embedding", "helper_llm"],
                       help="Filter models by slot type (embedding vs LLM)")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status()
    elif args.command == "add":
        cmd_add(args)
    elif args.command == "assign":
        cmd_assign(args)
    elif args.command == "validate":
        cmd_validate()
    elif args.command == "claude-status":
        cmd_claude_status()
    elif args.command == "list-models":
        cmd_list_models(args)


if __name__ == "__main__":
    main()
