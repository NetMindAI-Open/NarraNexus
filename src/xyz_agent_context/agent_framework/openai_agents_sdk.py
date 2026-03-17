"""
@file_name: openai_agents_sdk.py
@author: NetMind.AI
@date: 2025-11-07
@description: This file contains the openai agents sdk.
"""

from typing import AsyncGenerator, Optional

from agents import Agent, Runner, OpenAIChatCompletionsModel
from loguru import logger
from pydantic import BaseModel
from openai import AsyncOpenAI

from xyz_agent_context.settings import settings
from xyz_agent_context.utils.cost_tracker import record_cost, get_cost_context


MODEL_NAME = "gpt-5.1-2025-11-13"


class OpenAIAgentsSDK:
    def __init__(self):
        pass

    async def agent_loop(self) -> AsyncGenerator[str, None]:
        pass

    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: BaseModel = None,
        model: str = None,
        agent_id: Optional[str] = None,
        db=None,
    ) -> str:

        model_name = model or MODEL_NAME

        agent = Agent(
            name="ChatGPT",
            instructions=instructions,
            output_type=output_type,
            model=OpenAIChatCompletionsModel(
                model=model_name,
                openai_client=AsyncOpenAI(api_key=settings.openai_api_key),
            ),
        )

        result = await Runner.run(agent, user_input)

        # Resolve cost context: explicit params > global context
        _agent_id, _db = agent_id, db
        if not _agent_id or not _db:
            ctx = get_cost_context()
            if ctx:
                _agent_id, _db = ctx

        if _agent_id and _db:
            try:
                input_tokens = 0
                output_tokens = 0
                for raw_resp in getattr(result, "raw_responses", []):
                    usage = getattr(raw_resp, "usage", None)
                    if usage:
                        input_tokens += getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
                        output_tokens += getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
                if input_tokens > 0 or output_tokens > 0:
                    await record_cost(
                        db=_db,
                        agent_id=_agent_id,
                        event_id=None,
                        call_type="llm_function",
                        model=model_name,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
            except Exception as e:
                logger.warning(f"Failed to record OpenAI cost: {e}")

        return result
