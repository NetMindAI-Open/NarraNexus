"""
@file_name: anthropic_helper_sdk.py
@author: NarraNexus
@date: 2026-06-10
@description: Anthropic-protocol helper_llm caller (Messages API)

Mirror of OpenAIAgentsSDK's helper interface (llm_function / llm_stream)
for the case where the helper_llm slot points at an anthropic-protocol
provider — the single-Claude-key onboarding path. Call sites obtain the
right implementation via helper_sdk.get_helper_sdk(); they never import
this class directly.

Structured output: Anthropic's Messages API has no response_format /
json_schema parameter, so this SDK always uses the prompt-engineered
path (schema embedded in the system prompt, JSON extracted and
validated client-side) — the same mechanism as OpenAIAgentsSDK's
level-3 "prompt_only" fallback, reusing its extractor and result
wrappers so downstream consumers see identical shapes.
"""

import json
from typing import AsyncGenerator, Optional, Type

from loguru import logger
from pydantic import BaseModel, TypeAdapter
from anthropic import AsyncAnthropic

from xyz_agent_context.agent_framework.api_config import anthropic_helper_config
from xyz_agent_context.agent_framework.openai_agents_sdk import (
    _SimpleResult,
    _ParsedResult,
    _extract_json_from_llm_output,
    _last_llm_call_info,
)
from xyz_agent_context.utils.cost_tracker import (
    get_cost_context,
    record_cost,
    warn_missing_usage,
)
from xyz_agent_context.utils.logging import timed


# Messages API requires max_tokens on every call; helper outputs
# (entity extraction, narrative updates, fallback replies) are small.
_DEFAULT_MAX_TOKENS = 4096

_OFFICIAL_ANTHROPIC_BASE_URLS = {"", "https://api.anthropic.com", "https://api.anthropic.com/"}


class AnthropicHelperSDK:
    """Helper-LLM client speaking Anthropic's Messages API.

    Interface-compatible with OpenAIAgentsSDK's llm_function /
    llm_stream so the ~15 helper call sites work unchanged through
    get_helper_sdk().
    """

    def __init__(self):
        pass

    @staticmethod
    def _resolve_model(requested_model: str | None) -> str:
        """Resolve the model for this call.

        Unlike the OpenAI helper, per-call-site ``model=`` overrides are
        IGNORED here: call sites configure OpenAI model names (e.g. the
        narrative judge's gpt-5.4-mini), which don't exist on Anthropic
        endpoints. The slot's model always wins; the "default" sentinel
        falls back to the dataclass default (claude-haiku-4-5).
        """
        slot_model = anthropic_helper_config.model
        if slot_model and slot_model != "default":
            if requested_model and requested_model != slot_model:
                logger.debug(
                    f"[AnthropicHelper] ignoring per-call model "
                    f"{requested_model!r} (OpenAI-flavored); using slot "
                    f"model {slot_model!r}"
                )
            return slot_model
        from xyz_agent_context.agent_framework.api_config import AnthropicHelperConfig
        return AnthropicHelperConfig.model

    @staticmethod
    def _build_client() -> AsyncAnthropic:
        """Build the AsyncAnthropic client from the per-task config.

        auth_type "bearer_token" (e.g. NetMind's anthropic row) sends
        ``Authorization: Bearer`` via the SDK's auth_token param;
        everything else uses the standard x-api-key header.
        """
        kwargs: dict = {}
        if anthropic_helper_config.auth_type == "bearer_token":
            kwargs["auth_token"] = anthropic_helper_config.api_key
        else:
            kwargs["api_key"] = anthropic_helper_config.api_key
        if anthropic_helper_config.base_url:
            kwargs["base_url"] = anthropic_helper_config.base_url
        return AsyncAnthropic(**kwargs)

    @staticmethod
    def _max_tokens_for(model_name: str) -> int:
        from xyz_agent_context.agent_framework.model_catalog import get_max_output_tokens
        return get_max_output_tokens(model_name) or _DEFAULT_MAX_TOKENS

    async def agent_loop(self) -> AsyncGenerator[str, None]:
        pass

    @timed("llm.anthropic_helper.llm_function", slow_threshold_ms=10000)
    async def llm_function(
        self,
        instructions: str,
        user_input: str,
        output_type: Type[BaseModel] = None,
        model: str = None,
        agent_id: Optional[str] = None,
        db=None,
        reasoning_effort: Optional[str] = None,
    ):
        """Call the Anthropic helper with instructions and user input.

        ``reasoning_effort`` is accepted for interface parity and
        clamped (logged, never raised) — the Messages API has no
        equivalent per-call knob and the platform never errors on a
        user's parameter choice (iron rule #15).
        """
        model_name = self._resolve_model(model)
        max_tokens = self._max_tokens_for(model_name)
        if reasoning_effort:
            logger.debug(
                f"[AnthropicHelper] reasoning_effort={reasoning_effort!r} "
                f"has no Messages-API equivalent; ignored"
            )

        system_prompt = instructions
        schema_obj: Optional[dict] = None
        if output_type:
            schema_obj = output_type.model_json_schema()
            system_prompt += (
                "\n\nYou MUST respond with ONLY a valid JSON object matching "
                "this schema. No markdown, no code blocks, no explanation, "
                "no <think> tags. ONLY the raw JSON object.\n"
                f"Schema: {json.dumps(schema_obj, ensure_ascii=False)}"
            )

        client = self._build_client()
        logger.debug(
            f"[AnthropicHelper] Calling: model={model_name}, "
            f"base_url={anthropic_helper_config.base_url or '(official)'}, "
            f"max_tokens={max_tokens}, "
            f"output_type={output_type.__name__ if output_type else 'None'}"
        )

        # Stream internally and collect the final Message. The Anthropic
        # SDK REFUSES a non-streaming ``create`` when max_tokens is large
        # enough to risk a >10-minute operation ("Streaming is required for
        # operations that may take longer than 10 minutes"). Helper inputs
        # routinely run tens of thousands of tokens (narrative continuity,
        # consolidation), so the plain create path raised on every large
        # call. ``stream().get_final_message()`` returns the same Message
        # shape (content + usage) the rest of this method expects.
        async with client.messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            system=system_prompt,
            messages=[{"role": "user", "content": user_input}],
        ) as stream:
            resp = await stream.get_final_message()

        raw_content = "".join(
            block.text for block in resp.content if getattr(block, "type", "") == "text"
        )

        # Cost accounting — same hooks as the OpenAI helper.
        usage = getattr(resp, "usage", None)
        input_tokens = getattr(usage, "input_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or 0
        _agent_id, _db = self._resolve_cost_context(agent_id, db)
        if _agent_id and _db:
            if input_tokens > 0 or output_tokens > 0:
                try:
                    await record_cost(
                        db=_db, agent_id=_agent_id, event_id=None,
                        call_type="llm_function", model=model_name,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )
                except Exception as e:
                    logger.warning(f"[AnthropicHelper] failed to record cost: {e}")
            else:
                warn_missing_usage("AnthropicHelper", model_name, "llm_function")

        if not output_type:
            _last_llm_call_info.set({"model": model_name, "structured": "no_schema"})
            return _SimpleResult(raw_content, resp)

        json_str = _extract_json_from_llm_output(raw_content)
        if json_str is None:
            logger.warning(
                f"[AnthropicHelper] could not extract JSON from "
                f"{model_name}: head={raw_content[:200]!r}"
            )
            raise ValueError(
                f"Could not extract JSON from LLM response "
                f"(model={model_name}): {raw_content[:200]}"
            )

        adapter = TypeAdapter(output_type)
        try:
            parsed = adapter.validate_json(json_str)
        except Exception as e:
            logger.warning(
                f"[AnthropicHelper] schema validation failed on "
                f"{model_name}: {e!r} json={json_str[:200]!r}"
            )
            raise
        _last_llm_call_info.set(
            {"model": model_name, "structured": "anthropic_prompt"}
        )
        return _ParsedResult(parsed, raw_content, resp)

    @timed("llm.anthropic_helper.llm_stream", slow_threshold_ms=10000)
    async def llm_stream(
        self,
        instructions: str,
        user_input: str,
        model: str = None,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a helper reply delta-by-delta as plain text.

        Mirrors OpenAIAgentsSDK.llm_stream: no schema, plain user-facing
        text, cost recorded on completion.
        """
        model_name = self._resolve_model(model)
        _last_llm_call_info.set({"model": model_name, "structured": "stream"})
        max_tokens = self._max_tokens_for(model_name)
        if reasoning_effort:
            logger.debug(
                f"[AnthropicHelper-Stream] reasoning_effort="
                f"{reasoning_effort!r} ignored (no Messages-API equivalent)"
            )

        client = self._build_client()
        input_tokens = 0
        output_tokens = 0
        char_count = 0

        async with client.messages.stream(
            model=model_name,
            max_tokens=max_tokens,
            system=instructions,
            messages=[{"role": "user", "content": user_input}],
        ) as stream:
            async for delta in stream.text_stream:
                if delta:
                    char_count += len(delta)
                    yield delta
            final = await stream.get_final_message()
            usage = getattr(final, "usage", None)
            input_tokens = getattr(usage, "input_tokens", 0) or 0
            output_tokens = getattr(usage, "output_tokens", 0) or 0

        logger.info(
            f"[AnthropicHelper-Stream] completed: model={model_name} "
            f"chars={char_count} input_tokens={input_tokens} "
            f"output_tokens={output_tokens}"
        )

        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db:
            if input_tokens > 0 or output_tokens > 0:
                try:
                    await record_cost(
                        db=_db, agent_id=_agent_id, event_id=None,
                        call_type="llm_stream", model=model_name,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )
                except Exception as e:
                    logger.warning(
                        f"[AnthropicHelper-Stream] failed to record cost: {e}"
                    )
            else:
                warn_missing_usage("AnthropicHelper-Stream", model_name, "llm_stream")

    def _resolve_cost_context(self, agent_id, db):
        _agent_id, _db = agent_id, db
        if not _agent_id or not _db:
            ctx = get_cost_context()
            if ctx:
                _agent_id, _db = ctx
        return _agent_id, _db
