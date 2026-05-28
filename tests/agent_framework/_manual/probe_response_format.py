"""Probe NetMind response_format support per model.

Tests each model's behavior with 3 different structured-output strategies:
1. prompt_only — no response_format flag, just prompt engineering
2. json_object — response_format={"type":"json_object"}
3. json_schema — response_format={"type":"json_schema", strict=True, schema=...}

For each, records:
- whether the API accepted the request
- whether the returned content was non-empty
- whether the returned content was valid JSON
- whether the returned JSON matched the requested schema
"""
import os
import asyncio
import json
import sys
sys.path.insert(0, "/home/bin.liang/Documents/03-open-source/NarraNexus-deploy/NarraNexus")
from dotenv import load_dotenv

load_dotenv("/home/bin.liang/Documents/03-open-source/NarraNexus-deploy/NarraNexus/.env")
from openai import AsyncOpenAI

SCHEMA = {
    "type": "object",
    "properties": {
        "is_continuous": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["is_continuous", "reason"],
    "additionalProperties": False,
}

SYS = (
    "You are a topic-continuity classifier. Output ONLY a JSON object "
    "with exactly two keys: `is_continuous` (boolean) and `reason` (string)."
)
USR = (
    "Current narrative topic: 'ordering pizza'. New user message: "
    "'I want to add extra cheese'. Is this a continuation? Answer in JSON."
)

def classify(content: str) -> dict:
    """Categorise the API output."""
    out = {"empty": False, "valid_json": False, "schema_match": False, "head": ""}
    if not content:
        out["empty"] = True
        return out
    out["head"] = content[:200]
    try:
        obj = json.loads(content)
        out["valid_json"] = True
        if isinstance(obj, dict) and \
                "is_continuous" in obj and isinstance(obj["is_continuous"], bool) and \
                "reason" in obj and isinstance(obj["reason"], str):
            out["schema_match"] = True
    except Exception:
        # try to extract JSON from prose
        import re
        m = re.search(r"\{[\s\S]*?\}", content)
        if m:
            try:
                obj = json.loads(m.group())
                out["valid_json"] = True  # found JSON-in-prose
                if "is_continuous" in obj and "reason" in obj:
                    out["schema_match"] = True
            except Exception:
                pass
    return out


async def probe_one(client, model, mode):
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": USR}]
    kwargs = {"model": model, "messages": msgs, "max_tokens": 800}
    if mode == "json_object":
        kwargs["response_format"] = {"type": "json_object"}
    elif mode == "json_schema":
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {"name": "ContinuityResult", "strict": True, "schema": SCHEMA},
        }
    try:
        r = await client.chat.completions.create(**kwargs)
        content = r.choices[0].message.content or ""
        return {"api_ok": True, "error": None, **classify(content)}
    except Exception as e:
        return {"api_ok": False, "error": str(e)[:200], "empty": True, "valid_json": False, "schema_match": False, "head": ""}


async def main():
    client = AsyncOpenAI(
        api_key=os.getenv("NETMIND_API_KEY"),
        base_url="https://api.netmind.ai/inference-api/openai/v1",
    )
    models = [
        "deepseek-ai/DeepSeek-V3.1",
        "deepseek-ai/DeepSeek-V4-Flash",
        "deepseek-ai/DeepSeek-V4-Pro",
        "deepseek-ai/DeepSeek-V3",
        "deepseek-ai/DeepSeek-R1",
    ]
    modes = ["prompt_only", "json_object", "json_schema"]

    print(f"{'model':<38} {'mode':<14} {'api':<4} {'empty':<6} {'json':<5} {'schema':<7} {'head'}")
    print("-" * 130)
    for m in models:
        for mode in modes:
            r = await probe_one(client, m, mode)
            row = (
                f"{m:<38} {mode:<14} "
                f"{'ok' if r['api_ok'] else 'FAIL':<4} "
                f"{'yes' if r.get('empty') else 'no':<6} "
                f"{'yes' if r.get('valid_json') else 'no':<5} "
                f"{'yes' if r.get('schema_match') else 'no':<7} "
                f"{(r.get('head') or r.get('error') or '')[:80]!r}"
            )
            print(row)
        print()


asyncio.run(main())
