"""
@file_name: nexus_power_vs_claude_bench.py
@author: Bin Liang
@date: 2026-07-29
@description: Same-context comparison of NexusPower vs claude_code —
now with real platform modules.

v2 adds module scenarios against the LIVE local module MCP servers
(the local sqlite stack): self-cognition update (awareness), research
(web_search), artifact building (files + register_artifact), plus a
NexusPower-only dynamic-activation mode where modules arrive as
Expandables and the agent must expand_capability before use.

Per-agent semantics ride on explicit agent_id/user_id tool arguments;
the system prompt carries the identity block exactly like the
platform's materialized prompt does.

Safety: throwaway sqlite DATABASE_URL forced BEFORE platform imports
(module servers themselves talk to their own local-stack DB).

Usage:
    uv run python scripts/spikes/nexus_power_vs_claude_bench.py <scenario> [framework]
    scenario:  chat | tool | cognition | research | artifact | expand
    framework: claude_code | nexus_power | both (default both;
               'expand' is nexus_power-only)
Env: BENCH_MODEL (default deepseek-ai/DeepSeek-V4-Pro)
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="nexus_bench_")
os.environ["DATABASE_URL"] = f"sqlite:///{_TMP}/bench.db"
os.environ["NARRANEXUS_DEPLOYMENT_MODE"] = "local"
os.environ.setdefault("NEXUS_POWER_INPROCESS", "0")  # exercise the real runner

from xyz_agent_context.agent_framework import get_agent_loop_driver  # noqa: E402
from xyz_agent_context.agent_framework.api_config import (  # noqa: E402
    ClaudeConfig,
    OpenAIConfig,
    set_user_config,
)

AGENT_ID = "agent_aebcff787724"  # 小量 (binliang's local agent)
USER_ID = "binliang"

MCP = {
    "chat_module": {"url": "http://localhost:7804/sse"},
    "awareness_module": {"url": "http://localhost:7801/sse"},
    "common_tools_module": {"url": "http://localhost:7807/sse"},
}

# The delivery surface, declared explicitly (the platform passes this per
# turn in production; the adapter no longer guesses from server names).
REPLY_TOOLS = ["mcp__chat_module__send_message_to_user_directly"]

IDENTITY = (
    f"Identity: your agent_id is `{AGENT_ID}`; you serve the user whose "
    f"user_id is `{USER_ID}`. Pass these ids to platform tools that "
    "require them.\n"
    "To speak to the user, call "
    "`mcp__chat_module__send_message_to_user_directly` with agent_id, "
    "user_id and content — plain text is your private reasoning and is "
    "never delivered."
)

SYSTEM_PROMPT = (
    "You are Nova, a capable personal agent. You work inside your own "
    "workspace and act through tools. Be precise and economical.\n\n"
    + IDENTITY
)

SCENARIOS: dict[str, dict] = {
    "chat": {
        "prompt": "Introduce yourself briefly (under 120 words) — send it to me as a message.",
        "mcp": ["chat_module"],
    },
    "tool": {
        "prompt": (
            "Create a file named notes/summary.txt containing exactly three "
            "lines: alpha, beta, gamma. Read it back to verify, then message "
            "me the confirmed line count."
        ),
        "mcp": ["chat_module"],
    },
    "cognition": {
        "prompt": (
            "Update your self-awareness: add one trait — you prefer "
            "explaining technical concepts through analogies. Keep every "
            "existing trait intact (append, don't replace). Then message me "
            "a one-line summary of what you changed."
        ),
        "mcp": ["chat_module", "awareness_module"],
    },
    "research": {
        "prompt": (
            "Research what 'prompt caching' means for LLM APIs. Use web "
            "search, then message me a 3-sentence summary with one concrete "
            "number you found."
        ),
        "mcp": ["chat_module", "common_tools_module"],
    },
    "artifact": {
        "prompt": (
            "Build a single-file HTML page titled 'NexusPower Demo' with a "
            "short styled introduction paragraph. Save it as demo/index.html "
            "in your workspace, register it as an artifact (kind: html), "
            "then message me the entry path you registered."
        ),
        "mcp": ["chat_module", "common_tools_module"],
    },
}

# NexusPower-only: modules arrive as Expandables; the agent must activate
# them mid-turn via expand_capability before use.
EXPAND_SCENARIO = {
    "prompt": SCENARIOS["research"]["prompt"],
    "resident_mcp": ["chat_module"],
    "expandables": [
        {
            "key": "web",
            "card": "web search and URL reading tools",
            "instructions": "Web tools are now active. Cite what you searched.",
            "mcp_servers": {"common_tools_module": MCP["common_tools_module"]},
        },
        {
            "key": "self_awareness",
            "card": "inspect and update your own self-awareness",
            "instructions": "Awareness tools are now active.",
            "mcp_servers": {"awareness_module": MCP["awareness_module"]},
        },
    ],
}


def _load_provider() -> ClaudeConfig:
    model = os.environ.get("BENCH_MODEL", "deepseek-ai/DeepSeek-V4-Pro")
    config_path = Path.home() / ".nexusagent" / "llm_config.json"
    data = json.loads(config_path.read_text())
    for provider in data.get("providers", {}).values():
        if (
            provider.get("protocol") == "anthropic"
            and provider.get("auth_type") in ("api_key", "bearer_token")
            and provider.get("api_key")
            and provider.get("is_active")
        ):
            print(
                f"[bench] provider: {provider.get('name')} model={model} "
                f"auth={provider.get('auth_type')}"
            )
            return ClaudeConfig(
                model=model,
                api_key=provider["api_key"],
                base_url=provider.get("base_url", ""),
                auth_type=provider["auth_type"],
            )
    raise SystemExit("no key-based anthropic-protocol provider in llm_config.json")


async def _consume(driver, messages, mcp_servers, stats, **kwargs):
    text_parts: list[str] = []
    started = time.perf_counter()
    async for event in driver.agent_loop(
        messages=messages, mcp_servers=mcp_servers, **kwargs
    ):
        stats["events"] += 1
        if stats["first_event_s"] is None:
            stats["first_event_s"] = round(time.perf_counter() - started, 3)
        etype = event.get("type")
        data = event.get("data") or {}
        item = event.get("item") or {}
        if etype == "raw_response_event":
            if data.get("type") == "response.text.delta":
                text_parts.append(data.get("delta", ""))
            elif data.get("type") == "response.done":
                stats["usage"] = data.get("usage") or {}
                stats["model"] = data.get("model", "")
            elif data.get("type") == "response.error":
                stats["errors"].append(
                    f"{data.get('error_type')}: {str(data.get('error_message'))[:200]}"
                )
        elif etype == "run_item_stream_event":
            itype = item.get("type")
            if itype == "tool_call_item":
                stats["tool_calls"].append(item.get("tool_name"))
            elif itype == "tool_call_output_item":
                if item.get("status") == "failed":
                    stats["tool_failures"].append(str(item.get("output"))[:150])
            elif itype == "thinking_item":
                stats["thinking_chars"] += len(item.get("content") or "")
    stats["wall_s"] = round(time.perf_counter() - started, 3)
    stats["text_chars"] = sum(len(t) for t in text_parts)
    stats["monologue_tail"] = "".join(text_parts)[-300:]


async def _run_one(framework: str, scenario: str, claude_cfg: ClaudeConfig) -> dict:
    set_user_config(claude_cfg, OpenAIConfig())
    workspace = Path(_TMP) / f"{framework}_{scenario}"
    workspace.mkdir(parents=True, exist_ok=True)
    driver = get_agent_loop_driver(framework, working_path=str(workspace))

    stats: dict = {
        "framework": framework,
        "scenario": scenario,
        "first_event_s": None,
        "tool_calls": [],
        "tool_failures": [],
        "errors": [],
        "text_chars": 0,
        "thinking_chars": 0,
        "usage": {},
        "events": 0,
    }
    if scenario == "expand":
        spec = EXPAND_SCENARIO
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": spec["prompt"]},
        ]
        mcp_servers = {n: MCP[n] for n in spec["resident_mcp"]}
        await _consume(
            driver, messages, mcp_servers, stats,
            expandables=spec["expandables"],
            expressive_tools=REPLY_TOOLS,
        )
    else:
        spec = SCENARIOS[scenario]
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": spec["prompt"]},
        ]
        mcp_servers = {n: MCP[n] for n in spec["mcp"]}
        await _consume(driver, messages, mcp_servers, stats, expressive_tools=REPLY_TOOLS)
    return stats


def _summary_line(r: dict) -> str:
    if "fatal" in r:
        return f"{r['framework']:>12}: FATAL {r['fatal']}"
    usage = r.get("usage") or {}
    return (
        f"{r['framework']:>12}: wall={r['wall_s']}s first={r['first_event_s']}s "
        f"tools={len(r['tool_calls'])} fail={len(r['tool_failures'])} "
        f"think={r['thinking_chars']}ch "
        f"in={usage.get('input_tokens')} out={usage.get('output_tokens')} "
        f"cache_r={usage.get('cache_read_input_tokens')} "
        f"cache_w={usage.get('cache_creation_input_tokens')} "
        f"errors={len(r['errors'])}"
    )


async def main() -> None:
    scenario = sys.argv[1] if len(sys.argv) > 1 else "chat"
    which = sys.argv[2] if len(sys.argv) > 2 else "both"
    frameworks = (
        ["nexus_power"]
        if scenario == "expand"
        else (["claude_code", "nexus_power"] if which == "both" else [which])
    )
    claude_cfg = _load_provider()
    print(f"[bench] scenario={scenario!r} workspace={_TMP}")

    repeat = int(os.getenv("BENCH_REPEAT", "1"))
    results = []
    for framework in frameworks:
        for round_index in range(repeat):
            print(f"\n===== {framework} / {scenario} (round {round_index + 1}) =====")
            try:
                result = await _run_one(framework, scenario, claude_cfg)
                result["round"] = round_index + 1
            except Exception as exc:  # noqa: BLE001 - bench reports, never dies
                result = {"framework": framework, "fatal": f"{type(exc).__name__}: {exc}"}
            results.append(result)
            print(json.dumps(result, ensure_ascii=False, indent=2)[:2500])

    print("\n===== summary =====")
    for r in results:
        print(_summary_line(r))


if __name__ == "__main__":
    asyncio.run(main())
