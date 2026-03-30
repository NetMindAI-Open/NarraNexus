"""
@file_name: openai_agents_sdk.py
@author: NetMind.AI
@date: 2025-11-07
@description: OpenAI-compatible LLM function caller

Supports two modes:
1. Structured output via OpenAI Agents SDK (for models that support response_format)
2. Prompt-guided JSON + manual parsing (fallback for models like minimax that
   return <think> blocks and ignore response_format)
"""

import json
import re
from typing import AsyncGenerator, Optional, Type

from loguru import logger
from pydantic import BaseModel, TypeAdapter
from openai import AsyncOpenAI

from xyz_agent_context.agent_framework.api_config import openai_config
from xyz_agent_context.utils.cost_tracker import record_cost, get_cost_context


def _extract_json_from_llm_output(text: str) -> Optional[str]:
    """
    Extract JSON from LLM output that may contain <think> blocks,
    markdown code fences, or other wrapper text.

    Handles:
    - <think>...</think> reasoning blocks (minimax, deepseek)
    - ```json ... ``` markdown code blocks
    - Plain JSON objects
    """
    # Strip <think>...</think> blocks
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
    # Strip markdown code fences
    text = re.sub(r"```(?:json)?\s*", "", text).strip()
    text = text.rstrip("`").strip()
    # Find the outermost JSON object or array
    for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
        match = re.search(pattern, text)
        if match:
            candidate = match.group()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    return None


# Models that have failed structured output — skip Agents SDK for these
_structured_output_blocklist: set[str] = set()


_OFFICIAL_OPENAI_BASE_URLS = {"", "https://api.openai.com/v1", "https://api.openai.com/v1/"}


class OpenAIAgentsSDK:
    def __init__(self):
        pass

    @staticmethod
    def _resolve_model(requested_model: str | None) -> str:
        """
        Resolve which model to use based on the provider endpoint and slot config.

        Three modes:
        1. Slot model is "default" + official OpenAI → honor per-call-site model
           (e.g., narrative uses gpt-4o-mini, instance decision uses gpt-4o-mini).
           This is the recommended mode for official OpenAI users.

        2. Slot model is a specific name + official OpenAI → force that model
           for ALL helper_llm calls. User explicitly chose this.

        3. Non-official endpoint → always use slot config model, because the
           endpoint may not support OpenAI model names.
        """
        is_official = openai_config.base_url in _OFFICIAL_OPENAI_BASE_URLS
        is_default = openai_config.model == "default"

        if is_official and is_default and requested_model:
            # Mode 1: let each call site pick its best model
            return requested_model

        if is_official and not is_default:
            # Mode 2: user forced a specific model
            return openai_config.model

        # Mode 3: non-official endpoint, use slot config
        return openai_config.model

    async def agent_loop(self) -> AsyncGenerator[str, None]:
        pass

    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: Type[BaseModel] = None,
        model: str = None,
        agent_id: Optional[str] = None,
        db=None,
    ):
        """
        Call an LLM with instructions and user input.

        When output_type is specified, attempts structured output via
        Agents SDK first. If that fails (e.g., model doesn't support
        response_format), the model is added to a blocklist and all
        subsequent calls skip straight to the fallback path.
        """
        model_name = self._resolve_model(model)

        # Resolve max_tokens from model catalog (per-model limit)
        # If model is not in catalog, leave as None — let the API use its own default
        from xyz_agent_context.agent_framework.model_catalog import get_max_output_tokens
        max_tokens = get_max_output_tokens(model_name)

        # Build AsyncOpenAI client
        client_kwargs: dict = {"api_key": openai_config.api_key}
        if openai_config.base_url:
            client_kwargs["base_url"] = openai_config.base_url
        openai_client = AsyncOpenAI(**client_kwargs)

        logger.debug(
            f"[HelperLLM] Calling: model={model_name}, "
            f"base_url={openai_config.base_url or '(official)'}, "
            f"max_tokens={max_tokens}, output_type={output_type.__name__ if output_type else 'None'}"
        )

        # Try Agents SDK structured output (skip if model is blocklisted)
        if output_type and model_name not in _structured_output_blocklist:
            try:
                result = await self._try_agents_sdk(
                    openai_client, model_name, instructions, user_input, output_type, max_tokens
                )
                await self._record_cost(result, model_name, agent_id, db)
                return result
            except Exception as e:
                _structured_output_blocklist.add(model_name)
                logger.info(
                    f"Model '{model_name}' does not support structured output, "
                    f"added to blocklist (will use fallback from now on): {e}"
                )

        # Fallback: direct chat completion + manual JSON parsing
        result = await self._fallback_chat_completion(
            openai_client, model_name, instructions, user_input, output_type, max_tokens
        )
        return result

    async def _try_agents_sdk(
        self, client, model_name, instructions, user_input, output_type, max_tokens: Optional[int]
    ):
        """Attempt structured output via OpenAI Agents SDK"""
        from agents import Agent, Runner, OpenAIChatCompletionsModel, ModelSettings

        settings = ModelSettings(max_tokens=max_tokens) if max_tokens else ModelSettings()
        agent = Agent(
            name="LLMFunction",
            instructions=instructions,
            output_type=output_type,
            model=OpenAIChatCompletionsModel(
                model=model_name,
                openai_client=client,
            ),
            model_settings=settings,
        )

        return await Runner.run(agent, user_input)

    async def _fallback_chat_completion(
        self, client: AsyncOpenAI, model_name: str,
        instructions: str, user_input: str,
        output_type: Optional[Type[BaseModel]] = None,
        max_tokens: Optional[int] = None,
    ):
        """
        Direct chat completion with prompt-guided JSON extraction.

        Appends JSON schema to the prompt so the model knows the expected format,
        then parses the response manually.
        """
        # Build messages
        system_prompt = instructions
        if output_type:
            schema = output_type.model_json_schema()
            system_prompt += (
                "\n\nYou MUST respond with ONLY a valid JSON object matching this schema. "
                "No markdown, no code blocks, no explanation, no <think> tags. "
                "ONLY the raw JSON object.\n"
                f"Schema: {json.dumps(schema, ensure_ascii=False)}"
            )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # Only pass max_tokens when explicitly configured in model catalog;
        # otherwise let the API use its own default to avoid invalid_value errors.
        token_kwargs = {}
        if max_tokens is not None:
            token_kwargs["max_completion_tokens"] = max_tokens

        try:
            resp = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                **token_kwargs,
            )
        except Exception:
            # Fallback: some older providers only support max_tokens
            fallback_kwargs = {"max_tokens": max_tokens} if max_tokens is not None else {}
            resp = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                **fallback_kwargs,
            )

        raw_content = resp.choices[0].message.content or ""
        input_tokens = getattr(resp.usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(resp.usage, "completion_tokens", 0) or 0

        # Record cost
        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db:
            try:
                await record_cost(
                    db=_db, agent_id=_agent_id, event_id=None,
                    call_type="llm_function", model=model_name,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                )
            except Exception as e:
                logger.warning(f"Failed to record cost: {e}")

        if not output_type:
            # No parsing needed, return a simple wrapper
            return _SimpleResult(raw_content, resp)

        # Parse JSON from response
        json_str = _extract_json_from_llm_output(raw_content)
        if json_str is None:
            raise ValueError(
                f"Could not extract JSON from LLM response: {raw_content[:200]}"
            )

        adapter = TypeAdapter(output_type)
        parsed = adapter.validate_json(json_str)
        return _ParsedResult(parsed, raw_content, resp)

    def _resolve_cost_context(self, agent_id, db):
        _agent_id, _db = agent_id, db
        if not _agent_id or not _db:
            ctx = get_cost_context()
            if ctx:
                _agent_id, _db = ctx
        return _agent_id, _db

    async def _record_cost(self, result, model_name, agent_id, db):
        _agent_id, _db = self._resolve_cost_context(agent_id, db)
        if not _agent_id or not _db:
            return
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
                    db=_db, agent_id=_agent_id, event_id=None,
                    call_type="llm_function", model=model_name,
                    input_tokens=input_tokens, output_tokens=output_tokens,
                )
        except Exception as e:
            logger.warning(f"Failed to record OpenAI cost: {e}")


class _SimpleResult:
    """Wrapper for non-structured output to match expected interface"""
    def __init__(self, text: str, raw_response):
        self.final_output = text
        self.raw_responses = [raw_response]


class _ParsedResult:
    """Wrapper for parsed structured output to match expected interface"""
    def __init__(self, parsed, raw_text: str, raw_response):
        self.final_output = parsed
        self.raw_text = raw_text
        self.raw_responses = [raw_response]
