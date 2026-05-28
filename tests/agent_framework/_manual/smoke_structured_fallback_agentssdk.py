"""End-to-end test of the new 3-level structured-output fallback.

Hits real NetMind models, validates each level of the ladder, and reports:
  - which level each (provider, model) settled on
  - whether the response parsed cleanly into a Pydantic schema
  - whether the capability cache learned the right "unsupported" levels
  - whether multiple consecutive calls reuse the cache (no repeat probing)

Run: .venv/bin/python /tmp/test_structured_fallback.py
"""

import asyncio
import os
import sys
from typing import Optional

sys.path.insert(0, "/home/bin.liang/Documents/03-open-source/NarraNexus-deploy/NarraNexus")
from dotenv import load_dotenv
load_dotenv("/home/bin.liang/Documents/03-open-source/NarraNexus-deploy/NarraNexus/.env")

from pydantic import BaseModel, Field

# Force the OpenAI config to point at NetMind before importing the SDK
os.environ["OPENAI_API_KEY"] = os.getenv("NETMIND_API_KEY", "")
os.environ["OPENAI_BASE_URL"] = "https://api.netmind.ai/inference-api/openai/v1"

# Disable cost recording (no DB available in this test harness)
from xyz_agent_context.agent_framework import api_config  # noqa: E402
api_config.openai_config.api_key = os.getenv("NETMIND_API_KEY")
api_config.openai_config.base_url = "https://api.netmind.ai/inference-api/openai/v1"
# Will be overridden per-call by passing model=
api_config.openai_config.model = "deepseek-ai/DeepSeek-V3.1"

from xyz_agent_context.agent_framework.openai_agents_sdk import (  # noqa: E402
    OpenAIAgentsSDK,
    _response_format_capability,
    _capability_key,
    _allowed_levels,
    _structured_output_blocklist,
)


# ── Test schemas ────────────────────────────────────────────────────────

class ContinuityResult(BaseModel):
    """Same shape narrative_service.ContinuityResult uses."""
    is_continuous: bool = Field(description="True if the new message continues the current topic")
    reason: str = Field(description="One short sentence")


class CitySummary(BaseModel):
    """A more complex schema with nested + list fields."""
    city: str
    population_millions: float
    highlights: list[str]


# ── Test inputs ─────────────────────────────────────────────────────────

CONTINUITY_INSTRUCTIONS = (
    "You are a topic continuity classifier. The current narrative topic is "
    "given in the user input. Decide whether the new user message belongs to "
    "the same topic. Output JSON with `is_continuous` (bool) and a one-sentence "
    "`reason`."
)
CONTINUITY_INPUT_YES = (
    "Current topic: 'planning Thursday meeting agenda'. "
    "New message: 'continue, follow the same format as last week'."
)
CONTINUITY_INPUT_NO = (
    "Current topic: 'planning Thursday meeting agenda'. "
    "New message: 'help me write a Python function that sorts a list'."
)
CITY_INSTRUCTIONS = (
    "Return a CitySummary JSON object for the city in the user message."
)
CITY_INPUT = "Tokyo"


# ── Runner ──────────────────────────────────────────────────────────────

MODELS = [
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V4-Pro",
    "deepseek-ai/DeepSeek-V3",
]


def fresh_state():
    """Reset capability cache + blocklist so each test sees a clean slate."""
    _response_format_capability.clear()
    _structured_output_blocklist.clear()


def cache_dump():
    return {
        f"{k[1]}": sorted(v) for k, v in _response_format_capability.items()
    }


async def run_once(sdk, model: str, schema, instructions: str, user_input: str) -> dict:
    """One end-to-end call. Returns dict with chosen level + parsed result."""
    # Reset per-call context info
    try:
        result = await sdk.llm_function(
            instructions=instructions,
            user_input=user_input,
            output_type=schema,
            model=model,
        )
        from xyz_agent_context.agent_framework.openai_agents_sdk import (
            get_last_llm_call_info,
        )
        info = get_last_llm_call_info() or {}
        parsed = result.final_output
        return {
            "ok": True,
            "structured": info.get("structured"),
            "response_format": info.get("response_format"),
            "parsed": parsed.model_dump() if hasattr(parsed, "model_dump") else str(parsed)[:200],
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


async def test_a_per_model_continuity():
    """T1: ContinuityResult (small schema, both true/false answers expected)."""
    print("=" * 78)
    print("T1 — ContinuityResult: each model, both inputs (yes-case + no-case)")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    for model in MODELS:
        fresh_state()
        for label, inp in [("yes-case", CONTINUITY_INPUT_YES),
                           ("no-case ", CONTINUITY_INPUT_NO)]:
            r = await run_once(sdk, model, ContinuityResult, CONTINUITY_INSTRUCTIONS, inp)
            print(f"  {model:<40} {label} → ok={r['ok']} "
                  f"struct={r.get('structured')} fmt={r.get('response_format')} "
                  f"parsed={r.get('parsed') or r.get('error')}")
        print(f"    capability cache after both calls: {cache_dump()}")
        print()


async def test_b_complex_schema():
    """T2: more complex CitySummary schema."""
    print("=" * 78)
    print("T2 — CitySummary (complex schema with list field)")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    for model in MODELS:
        fresh_state()
        r = await run_once(sdk, model, CitySummary, CITY_INSTRUCTIONS, CITY_INPUT)
        print(f"  {model:<40} → ok={r['ok']} "
              f"struct={r.get('structured')} fmt={r.get('response_format')}")
        if r["ok"]:
            print(f"    parsed: {r['parsed']}")
        else:
            print(f"    err: {r['error']}")
        print(f"    capability cache: {cache_dump()}")
        print()


async def test_c_cache_persistence():
    """T3: confirm the cache prevents repeat probing after first failure."""
    print("=" * 78)
    print("T3 — capability cache reuses the first probe (3 calls on V4-Flash)")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    fresh_state()
    model = "deepseek-ai/DeepSeek-V4-Flash"
    api_calls_per_run = []
    import openai.resources.chat.completions as cc
    orig_create = cc.AsyncCompletions.create
    counter = {"n": 0}

    async def counting_create(self, *args, **kwargs):
        counter["n"] += 1
        return await orig_create(self, *args, **kwargs)
    cc.AsyncCompletions.create = counting_create
    try:
        for i in range(3):
            counter["n"] = 0
            r = await run_once(sdk, model, ContinuityResult,
                               CONTINUITY_INSTRUCTIONS, CONTINUITY_INPUT_YES)
            api_calls_per_run.append(counter["n"])
            print(f"  call #{i+1}: API hops={counter['n']} "
                  f"struct={r.get('structured')} fmt={r.get('response_format')}")
            print(f"    cache: {cache_dump()}")
    finally:
        cc.AsyncCompletions.create = orig_create

    # Expected: call 1 = 2 API hops (Agents SDK try → fail → fallback ladder
    # tries json_schema (fail) → json_object (ok) = 1 fallback hop total
    # = 2 total). Wait, Agents SDK uses its own internal hop count.
    # Subsequent calls: blocklisted → 1 fallback hop using cached level.
    print()
    print(f"  API hops per call: {api_calls_per_run}")
    print(f"  Expected: 1st > 2nd ≈ 3rd (cache short-circuits the probing)")


async def test_d_default_model_path():
    """T4: end-to-end through OpenAIAgentsSDK.llm_function with the
    'default' model name + non-official endpoint (mirrors prod config)."""
    print("=" * 78)
    print("T4 — full llm_function() with default model resolution")
    print("=" * 78)
    fresh_state()
    sdk = OpenAIAgentsSDK()
    # Force the resolver to pick the slot config (DeepSeek-V4-Flash)
    api_config.openai_config.model = "deepseek-ai/DeepSeek-V4-Flash"
    r = await run_once(sdk, None, ContinuityResult, CONTINUITY_INSTRUCTIONS, CONTINUITY_INPUT_NO)
    print(f"  ok={r['ok']} struct={r.get('structured')} "
          f"fmt={r.get('response_format')}")
    print(f"  parsed: {r.get('parsed')}")


async def main():
    if not os.getenv("NETMIND_API_KEY"):
        print("ERROR: NETMIND_API_KEY missing from env")
        return
    print(f"using NetMind base_url: {api_config.openai_config.base_url}")
    print(f"key prefix: {api_config.openai_config.api_key[:8]}…")
    print()
    await test_a_per_model_continuity()
    await test_b_complex_schema()
    await test_c_cache_persistence()
    await test_d_default_model_path()


if __name__ == "__main__":
    asyncio.run(main())
