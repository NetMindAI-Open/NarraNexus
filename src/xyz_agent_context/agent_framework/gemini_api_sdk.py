"""
@file_name: gemini_api_sdk.py
@author: NetMind.AI
@date: 2025-12-04
@description: This file contains the gemini api sdk.
"""

from typing import Optional

from loguru import logger
from pydantic import BaseModel
from google import genai

from xyz_agent_context.agent_framework.api_config import gemini_config
from xyz_agent_context.utils.cost_tracker import record_cost, get_cost_context
from xyz_agent_context.utils.logging import timed


class GeminiAPISDK:

    def __init__(self, model: str | None = None):
        self.client = genai.Client(api_key=gemini_config.api_key)
        self.model = model or gemini_config.model

    @timed("llm.gemini.llm_function", slow_threshold_ms=10000)
    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: BaseModel = None,
        file_path: str = None,
        agent_id: Optional[str] = None,
        db=None,
    ) -> str:

        if file_path:
            if file_path.endswith(".pdf"):
                response = await self._make_response_with_pdf(instructions, user_input, output_type, file_path)
            else:
                raise ValueError(f"Unsupported file type: {file_path}")
        else:
            response = await self._make_response(instructions, user_input, output_type)

        # Extract token usage and record cost
        await self._record_usage(response, agent_id, db)

        return response

    async def _record_usage(self, response, agent_id: Optional[str], db) -> None:
        """Extract usage_metadata from Gemini response and record cost."""
        # Resolve cost context: explicit params > global context
        if not agent_id or not db:
            ctx = get_cost_context()
            if ctx:
                agent_id, db = ctx
        if not agent_id or not db:
            return
        try:
            usage = getattr(response, "usage_metadata", None)
            if usage:
                input_tokens = getattr(usage, "prompt_token_count", 0) or 0
                output_tokens = getattr(usage, "candidates_token_count", 0) or 0
                if input_tokens > 0 or output_tokens > 0:
                    await record_cost(
                        db=db,
                        agent_id=agent_id,
                        event_id=None,
                        call_type="llm_function",
                        model=self.model,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
        except Exception as e:
            logger.warning(f"Failed to record Gemini cost: {e}")

    async def _make_response_with_pdf(
        self,
        instructions: str,
        user_input: str,
        output_type: BaseModel = None,
        file_path: str = None,
    ) -> str:

        file = self.client.files.upload(file=file_path)

        prompt = f"""
# System Instructions
{instructions}

# User Input
{user_input}
"""

        if output_type:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[file, prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": output_type.model_json_schema(),
                },
            )
        else:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[file, prompt],
            )

        return response

    async def _make_response(
        self,
        instructions: str,
        user_input: str,
        output_type: BaseModel = None,
    ) -> str:

        prompt = f"""
# System Instructions
{instructions}

# User Input
{user_input}
"""

        if output_type:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[prompt],
                config={
                    "response_mime_type": "application/json",
                    "response_json_schema": output_type.model_json_schema(),
                },
            )
        else:
            response = self.client.models.generate_content(
                model=self.model,
                contents=[prompt],
            )

        return response
    


