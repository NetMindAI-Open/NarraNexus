"""
@file_name: smoke_gpt54_models.py
@description: Smoke test the gpt-5.4-{mini,nano} models via the helper-LLM
SDK with reasoning_effort. Calls each model once with a typical narrative
judge-shaped prompt and prints elapsed time. Catches obvious wiring
problems (bad model id, missing reasoning_effort plumbing, missing API
permission) before we point the live narrative system at them.
"""
import asyncio
import os
import time

from dotenv import load_dotenv

# Load .env so OPENAI_API_KEY is available; mirrors backend behavior.
load_dotenv()

from xyz_agent_context.agent_framework.openai_agents_sdk import OpenAIAgentsSDK  # noqa: E402
from xyz_agent_context.agent_framework.api_config import (  # noqa: E402
    set_user_config,
    OpenAIConfig,
    ClaudeConfig,
    EmbeddingConfig,
)
from pydantic import BaseModel  # noqa: E402


class SimpleJudge(BaseModel):
    matched_category: str
    reason: str


INSTRUCTIONS = (
    "You are a narrative routing judge. Pick which category the user's "
    "query belongs to, or say 'new'. Reply with {matched_category, reason}."
)

USER_INPUT = (
    "## Candidates:\n"
    "[Topic-0] Travel planning\n"
    "[Topic-1] Stock market\n"
    "## User's New Query:\n"
    "Where should I go for a weekend trip from Beijing?\n"
)


async def call_once(model: str, effort: str) -> tuple[float, str]:
    sdk = OpenAIAgentsSDK()
    start = time.monotonic()
    try:
        result = await sdk.llm_function(
            instructions=INSTRUCTIONS,
            user_input=USER_INPUT,
            output_type=SimpleJudge,
            model=model,
            reasoning_effort=effort or None,
        )
        elapsed = time.monotonic() - start
        parsed: SimpleJudge = result.final_output
        return elapsed, f"ok category={parsed.matched_category!r}"
    except Exception as e:
        elapsed = time.monotonic() - start
        return elapsed, f"ERROR {type(e).__name__}: {e}"


async def main():
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY not set")
    set_user_config(
        ClaudeConfig(api_key="unused", model="unused"),
        OpenAIConfig(api_key=api_key, model="default"),
        EmbeddingConfig(api_key=api_key, model="text-embedding-3-small"),
    )

    cases = [
        ("gpt-5.4-mini-2026-03-17", "low"),
        ("gpt-5.4-mini-2026-03-17", "none"),
        ("gpt-5.4-nano-2026-03-17", "low"),
        ("gpt-5.4-nano-2026-03-17", "none"),
        ("gpt-4o-mini", ""),
    ]
    for model, effort in cases:
        elapsed, status = await call_once(model, effort)
        print(f"  {model:30}  effort={effort or '-':6}  {elapsed*1000:7.0f} ms  {status}")


if __name__ == "__main__":
    asyncio.run(main())
