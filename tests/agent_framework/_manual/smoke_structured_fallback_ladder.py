"""Direct test of `_fallback_chat_completion` — bypasses Agents SDK so we
exercise the 3-level ladder unambiguously.

Pre-populates `_structured_output_blocklist` for every model under test so
`llm_function` always takes the fallback path.
"""
import asyncio
import os
import sys

sys.path.insert(0, "/home/bin.liang/Documents/03-open-source/NarraNexus-deploy/NarraNexus")
from dotenv import load_dotenv
load_dotenv("/home/bin.liang/Documents/03-open-source/NarraNexus-deploy/NarraNexus/.env")

from pydantic import BaseModel, Field

os.environ["OPENAI_API_KEY"] = os.getenv("NETMIND_API_KEY", "")
os.environ["OPENAI_BASE_URL"] = "https://api.netmind.ai/inference-api/openai/v1"

from xyz_agent_context.agent_framework import api_config  # noqa: E402
api_config.openai_config.api_key = os.getenv("NETMIND_API_KEY")
api_config.openai_config.base_url = "https://api.netmind.ai/inference-api/openai/v1"
api_config.openai_config.model = "deepseek-ai/DeepSeek-V3.1"

from xyz_agent_context.agent_framework.openai_agents_sdk import (  # noqa: E402
    OpenAIAgentsSDK,
    _structured_output_blocklist,
    _response_format_capability,
    get_last_llm_call_info,
)


class ContinuityResult(BaseModel):
    is_continuous: bool = Field(description="True if continuation")
    reason: str = Field(description="One short sentence")


class Nested(BaseModel):
    n: int
    items: list[str]


MODELS = [
    "deepseek-ai/DeepSeek-V3.1",
    "deepseek-ai/DeepSeek-V4-Flash",
    "deepseek-ai/DeepSeek-V4-Pro",
    "deepseek-ai/DeepSeek-V3",
]

INSTR = (
    "Decide whether the new message continues the current topic. "
    "Output JSON with is_continuous (bool) and one-sentence reason."
)
INP_YES = (
    "Current topic: 'planning Thursday meeting agenda'. "
    "New message: 'continue, same format as last week'."
)
INP_NO = (
    "Current topic: 'planning Thursday meeting agenda'. "
    "New message: 'help me write Python code that sorts a list'."
)


def force_fallback(*models):
    """Force agents_sdk to be skipped for these models."""
    for m in models:
        _structured_output_blocklist.add(m)


def fresh():
    _structured_output_blocklist.clear()
    _response_format_capability.clear()


async def run_once(sdk, model, schema, instr, inp):
    # NetMind is a non-official endpoint, so the resolver uses
    # api_config.openai_config.model regardless of what we pass as `model=`.
    # Update the slot config so each test really targets the model we asked for.
    if model is not None:
        api_config.openai_config.model = model
    try:
        result = await sdk.llm_function(
            instructions=instr,
            user_input=inp,
            output_type=schema,
            model=model,
        )
        info = get_last_llm_call_info() or {}
        parsed = result.final_output
        return {
            "ok": True,
            "structured": info.get("structured"),
            "response_format": info.get("response_format"),
            "parsed": parsed.model_dump() if hasattr(parsed, "model_dump") else None,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {str(e)[:200]}"}


def cache_dump():
    return {f"{k[1]}": sorted(v) for k, v in _response_format_capability.items()}


async def test_a_per_model_fallback():
    print("=" * 78)
    print("T1 — Forced fallback path: each model picks its highest supported level")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    for model in MODELS:
        fresh()
        force_fallback(model)
        for label, inp in [("yes", INP_YES), ("no ", INP_NO)]:
            r = await run_once(sdk, model, ContinuityResult, INSTR, inp)
            print(
                f"  {model:<40} {label} → ok={r['ok']} "
                f"struct={r.get('structured')} fmt={r.get('response_format')}"
            )
            if r["ok"]:
                print(f"    parsed: {r['parsed']}")
            else:
                print(f"    err: {r['error']}")
        print(f"    capability cache: {cache_dump()}")
        print()


async def test_b_cache_reuse():
    print("=" * 78)
    print("T2 — capability cache shortcuts subsequent calls (V4-Flash, 4 calls)")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    fresh()
    model = "deepseek-ai/DeepSeek-V4-Flash"
    force_fallback(model)
    import openai.resources.chat.completions as cc
    orig = cc.AsyncCompletions.create
    counter = {"n": 0}

    async def counting(self, *args, **kwargs):
        counter["n"] += 1
        return await orig(self, *args, **kwargs)
    cc.AsyncCompletions.create = counting
    try:
        for i in range(4):
            counter["n"] = 0
            r = await run_once(sdk, model, ContinuityResult, INSTR, INP_YES)
            print(
                f"  call #{i+1}: API hops={counter['n']} "
                f"struct={r.get('structured')} fmt={r.get('response_format')} "
                f"cache={cache_dump()}"
            )
    finally:
        cc.AsyncCompletions.create = orig


async def test_c_complex_schema_forced_fallback():
    print("=" * 78)
    print("T3 — Complex schema (list field) through forced fallback path")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    for model in MODELS:
        fresh()
        force_fallback(model)
        r = await run_once(
            sdk, model, Nested,
            "Return Nested JSON object with n=3 and items=['a','b','c'].",
            "Please return n=3, items=['a','b','c'].",
        )
        print(
            f"  {model:<40} → ok={r['ok']} "
            f"struct={r.get('structured')} fmt={r.get('response_format')}"
        )
        if r["ok"]:
            print(f"    parsed: {r['parsed']}")
        else:
            print(f"    err: {r['error']}")


async def test_d_unsupported_level_caching():
    """T4 — confirm json_schema gets dropped from cache after first 400 on V4."""
    print("=" * 78)
    print("T4 — V4-Flash should learn to skip json_schema after one failure")
    print("=" * 78)
    sdk = OpenAIAgentsSDK()
    fresh()
    model = "deepseek-ai/DeepSeek-V4-Flash"
    force_fallback(model)
    print("  before any call:")
    print(f"    capability cache: {cache_dump()}")
    print(f"    inferred allowed for {model}: would try json_schema, then json_object")
    r1 = await run_once(sdk, model, ContinuityResult, INSTR, INP_YES)
    print(f"  after call 1: struct={r1.get('structured')} fmt={r1.get('response_format')}")
    print(f"    capability cache: {cache_dump()}")
    expected = {model: ["json_object"]}
    actual_after = {f"{k[1]}": sorted(v) for k, v in _response_format_capability.items()}
    print(f"    expected json_schema dropped → cache should be: {expected}")
    print(f"    actual cache              : {actual_after}")
    print(f"    {'PASS' if actual_after == expected else 'FAIL'}")


async def main():
    print(f"NetMind base_url: {api_config.openai_config.base_url}")
    print()
    await test_a_per_model_fallback()
    await test_b_cache_reuse()
    await test_c_complex_schema_forced_fallback()
    await test_d_unsupported_level_caching()


if __name__ == "__main__":
    asyncio.run(main())
