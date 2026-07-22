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
from contextvars import ContextVar
from typing import AsyncGenerator, Optional, Type

from loguru import logger
from pydantic import BaseModel, TypeAdapter
from openai import AsyncOpenAI

from xyz_agent_context.agent_framework.api_config import openai_config
from xyz_agent_context.utils.cost_tracker import (
    get_cost_context,
    record_cost,
    warn_missing_usage,
)
from xyz_agent_context.utils.logging import timed


# Per-async-task scratchpad so an outer `with timed(...) as t` can read
# back which model + structured-output mode the inner SDK call ended up
# using. Set by llm_function / llm_stream after model resolution; read
# (and optionally tag) by the caller right after the await returns.
# Contextvars propagate across `await` within the same task without
# leaking across tasks, which matches our usage exactly.
_last_llm_call_info: ContextVar[Optional[dict]] = ContextVar(
    "_last_llm_call_info", default=None
)


def get_last_llm_call_info() -> Optional[dict]:
    """Return ``{model, structured}`` for the most recent llm_function /
    llm_stream call on this async task, or None if none has run.

    ``structured`` values:
      - ``"agents_sdk"`` — happy path, response_format honored
      - ``"fallback_first_fail"`` — caller asked for output_type, model
        rejected it on its first call, we added to blocklist and
        fell back to JSON-in-prompt parse (this call cost 2 LLM hops)
      - ``"fallback_blocklisted"`` — model already known to reject
        response_format, skipped straight to fallback (1 LLM hop)
      - ``"no_schema"`` — caller didn't pass output_type, no structured
        output attempted in the first place
      - ``"stream"`` — llm_stream call (always non-structured)

    Read this right after an SDK call to avoid being overwritten by any
    subsequent SDK call further down the same task.
    """
    return _last_llm_call_info.get()


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
    # Find the outermost JSON object or array. Greedy (first opener → last
    # closer) so a single nested object like {"a": {"b": 1}} is captured whole.
    for pattern in [r"\{[\s\S]*\}", r"\[[\s\S]*\]"]:
        match = re.search(pattern, text)
        if match:
            candidate = match.group()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
    # Fallback: the greedy span didn't parse — most often because the text
    # carries TWO concatenated objects ({...}{...}). This happens with the
    # codex CLI helper, whose stream repeats the message (streamed increments
    # + an item.completed full copy, sometimes slightly reworded), so greedy
    # grabs first-{ … last-} = both objects = invalid. Return the FIRST
    # balanced object/array instead (string- and escape-aware so braces inside
    # JSON strings don't miscount).
    return _first_balanced_json(text)


def _balanced_end(text: str, start: int) -> Optional[int]:
    """Index of the closer that balances the opener at ``text[start]``, matching
    ``{``↔``}`` and ``[``↔``]`` via a type-checked stack (so ``{"a": 1]`` is
    rejected, not mis-accepted), string/escape aware. ``None`` if unbalanced."""
    pairs = {"{": "}", "[": "]"}
    stack: list[str] = []
    in_str = False
    esc = False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            stack.append(pairs[c])
        elif c in "}]":
            if not stack or stack[-1] != c:
                return None  # mismatched closer → not balanced from here
            stack.pop()
            if not stack:
                return j
    return None


def _first_balanced_json(text: str) -> Optional[str]:
    """Return the FIRST balanced ``{...}`` / ``[...]`` substring that parses as
    JSON. Scans every opener in order; a candidate that balances but does not
    parse falls through to the next opener (instead of giving up)."""
    for i, c in enumerate(text):
        if c in "{[":
            end = _balanced_end(text, i)
            if end is not None:
                candidate = text[i:end + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    continue  # balanced but invalid → try the next opener
    return None


def json_repair_note(reason: str) -> str:
    """Build a re-prompt telling a prompt-engineered structured-output model to
    fix its last reply.

    Shared by the Claude helper SDKs (AnthropicHelperSDK / CliHelperSDK), whose
    structured output is prompt-engineered (schema in the prompt + client-side
    ``_extract_json_from_llm_output`` + ``validate_json``) rather than enforced
    by the API. Complex nested schemas on cheaper models (Haiku, CLI one-shot)
    sometimes come back wrapped in prose / markdown or schema-divergent; a
    bounded re-prompt recovers most. Kept generic (no scenario specifics) per
    铁律 #4.
    """
    return (
        "\n\nYour previous response was NOT valid JSON matching the required "
        f"schema ({reason}). Respond AGAIN with ONLY the raw JSON object — no "
        "prose, no explanation, no markdown, no code fences, no <think> tags. "
        "Output must start with '{' and end with '}' and satisfy the schema."
    )


# Provider x model combos whose Agents SDK structured-output path returned
# a CLEAR "unsupported" error. Keyed by (base_url, model) — see
# `_capability_key` — so the same model name on a different provider is
# judged independently (no cross-provider contamination). Only clear
# capability errors land here; transient network / 5xx failures never do,
# so a single blip cannot permanently downgrade a model (incident lesson #3).
_structured_output_blocklist: set[tuple[str, str]] = set()


# ── Provider × model capability map for the prompt-fallback path ─────────
#
# When the Agents SDK structured-output path fails for a model
# (`_structured_output_blocklist`), we fall back to chat.completions.create
# with `response_format`. There are three increasingly-lenient levels we
# try, in this order:
#
#   1. ``response_format = {"type": "json_schema", "json_schema": …}``
#      Strict schema enforcement at the API layer. Most reliable when the
#      provider supports it (OpenAI, Anyscale, Together, DeepSeek-V3.1
#      do; DeepSeek-V4 series rejects with 400 "response_format type
#      unavailable").
#   2. ``response_format = {"type": "json_object"}``
#      Forces the API to return SOME valid JSON object (no prose). The
#      provider doesn't validate the schema, but our subsequent
#      `_extract_json_from_llm_output` + `validate_json` does. Supported
#      by every DeepSeek model we have tested on NetMind, plus the
#      official OpenAI endpoint.
#   3. No ``response_format``
#      The original prompt-engineering-only path. Last resort.
#
# We cache per-(base_url, model) which levels we have proven NOT to work
# so subsequent calls skip the failed attempts. The cache only grows on
# clear "unsupported" errors — transient network / 5xx failures do NOT
# downgrade a level.
#
# Key: (base_url, model_name). Value: the set of levels still believed
# to work for this combination. Default (key absent) means "no probe
# yet, try them all in order".
_response_format_capability: dict[tuple[str, str], set[str]] = {}


def _capability_key(model_name: str) -> tuple[str, str]:
    return ((openai_config.base_url or "").rstrip("/"), model_name)


def _allowed_levels(key: tuple[str, str]) -> set[str]:
    """Return the set of response_format levels still believed to work
    for this (base_url, model). Defaults to ALL when no probe yet."""
    return _response_format_capability.get(key, {"json_schema", "json_object"})


def _mark_unsupported(key: tuple[str, str], level: str) -> None:
    """Drop a level from the capability set after the API rejected it
    with a clear 'unsupported response_format type' error."""
    current = set(_allowed_levels(key))
    current.discard(level)
    _response_format_capability[key] = current
    logger.info(
        f"[StructuredFallback] cached unsupported: provider={key[0]} "
        f"model={key[1]} level={level} (remaining={sorted(current) or 'none'})"
    )


async def _audit_framework_downgrade(event_type: str, detail: dict) -> None:
    """Best-effort DB audit of a framework self-downgrade (incident lesson
    #4/#5). The platform silently re-routing to a slower/fallback path is
    exactly the kind of degradation that vanishes from logs on docker
    restart and is otherwise invisible. Writes to the shared `service_audit`
    table under service="llm_framework". NEVER raises — a logger-only
    fallback covers any audit failure (the observer must not break the
    observed)."""
    try:
        from xyz_agent_context.repository.service_audit_repository import (
            ServiceAuditRepository,
        )
        from xyz_agent_context.utils.db_factory import get_db_client
        repo = ServiceAuditRepository(await get_db_client())
        await repo.record("llm_framework", event_type, detail)
    except Exception as e:  # noqa: BLE001 — audit is advisory
        logger.warning(f"[StructuredFallback] downgrade audit write failed: {e}")


def _is_response_format_unsupported_error(exc: Exception) -> bool:
    """Detect whether an error is provider saying 'I don't support
    this response_format type'. Heuristic — matches the patterns we
    have observed in production. False positives are safe (worst case
    we cache a non-existent capability), false negatives just waste
    one retry attempt next call."""
    msg = str(exc).lower()
    if "response_format" in msg or "response format" in msg:
        return True
    # NetMind's exact wording for the V4 series rejection.
    if "this response_format type is unavailable" in msg:
        return True
    # OpenAI-style schema rejections (json_schema strict mismatches).
    if "json_schema" in msg and ("unavailable" in msg or "unsupported" in msg):
        return True
    return False


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

        "default" is never a real model name — it's a UI sentinel meaning
        "use the system preset". This method guarantees the return value
        is always a concrete model identifier.
        """
        is_official = openai_config.base_url in _OFFICIAL_OPENAI_BASE_URLS
        is_default = openai_config.model == "default"

        if is_default:
            # "default" → use per-call-site model if provided, otherwise
            # fall back to the system preset (OpenAIConfig dataclass default)
            from xyz_agent_context.agent_framework.api_config import OpenAIConfig
            fallback = requested_model or OpenAIConfig.model
            return fallback

        if is_official:
            # Mode 2: user forced a specific model on official endpoint
            return openai_config.model

        # Mode 3: non-official endpoint, use slot config
        return openai_config.model

    async def agent_loop(self) -> AsyncGenerator[str, None]:
        pass

    @timed("llm.openai.llm_function", slow_threshold_ms=10000)
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
        """
        Call an LLM with instructions and user input.

        When output_type is specified, attempts structured output via
        Agents SDK first. If that fails (e.g., model doesn't support
        response_format), the model is added to a blocklist and all
        subsequent calls skip straight to the fallback path.

        reasoning_effort: optional per-call reasoning budget for
        reasoning-capable models (gpt-5*, o-series). Valid values:
        "none" / "low" / "medium" / "high" / "xhigh". Non-reasoning
        models will reject this parameter — caller is responsible for
        only passing it when the model supports it.
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
            f"max_tokens={max_tokens}, reasoning_effort={reasoning_effort}, "
            f"output_type={output_type.__name__ if output_type else 'None'}"
        )

        # Try Agents SDK structured output (skip if this provider x model
        # is blocklisted). On failure we always fall through to the fallback
        # for THIS call, but only PERMANENTLY blocklist when the error
        # clearly says the model can't do structured output — a transient
        # blip must not strand the model on the fallback path forever
        # (incident lesson #3).
        first_attempt_failed = False
        cap_key = _capability_key(model_name)
        if output_type and cap_key not in _structured_output_blocklist:
            try:
                result = await self._try_agents_sdk(
                    openai_client, model_name, instructions, user_input,
                    output_type, max_tokens, reasoning_effort,
                )
                await self._record_cost(result, model_name, agent_id, db)
                _last_llm_call_info.set(
                    {"model": model_name, "structured": "agents_sdk"}
                )
                return result
            except Exception as e:
                first_attempt_failed = True
                if _is_response_format_unsupported_error(e):
                    _structured_output_blocklist.add(cap_key)
                    logger.info(
                        f"[StructuredFallback] Agents SDK structured output "
                        f"unsupported for provider={cap_key[0]} model={model_name}; "
                        f"blocklisted, using fallback henceforth: {e}"
                    )
                    await _audit_framework_downgrade(
                        "agents_sdk_blocklisted",
                        {
                            "base_url": cap_key[0],
                            "model": model_name,
                            "error": str(e)[:500],
                        },
                    )
                else:
                    logger.info(
                        f"[StructuredFallback] Agents SDK attempt failed for "
                        f"model={model_name} (transient/other — NOT blocklisted, "
                        f"will retry SDK next call): {e}"
                    )

        # Fallback: direct chat completion + manual JSON parsing
        result = await self._fallback_chat_completion(
            openai_client, model_name, instructions, user_input,
            output_type, max_tokens, reasoning_effort,
        )
        if not output_type:
            structured_mode = "no_schema"
        elif first_attempt_failed:
            structured_mode = "fallback_first_fail"
        else:
            structured_mode = "fallback_blocklisted"
        _last_llm_call_info.set(
            {"model": model_name, "structured": structured_mode}
        )
        return result

    @timed("llm.openai.llm_stream", slow_threshold_ms=10000)
    async def llm_stream(
        self,
        instructions: str,
        user_input: str,
        model: str = None,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a helper_llm response delta-by-delta as plain text.

        Used by the chat fallback path (agent finished without calling
        send_message_to_user_directly): we ask the helper_llm to produce
        a real reply for the user based on the agent's reasoning, and
        yield each token-delta so the websocket can push it to the
        frontend exactly like a normal agent reply.

        No structured output, no schema enforcement — this is for plain
        user-facing text. Cost is logged on stream completion.

        Yields:
            str: each content delta from the underlying chat completion.
        """
        model_name = self._resolve_model(model)
        _last_llm_call_info.set({"model": model_name, "structured": "stream"})
        from xyz_agent_context.agent_framework.model_catalog import get_max_output_tokens
        max_tokens = get_max_output_tokens(model_name)

        client_kwargs: dict = {"api_key": openai_config.api_key}
        if openai_config.base_url:
            client_kwargs["base_url"] = openai_config.base_url
        openai_client = AsyncOpenAI(**client_kwargs)

        messages = [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_input},
        ]

        # Mirror llm_function's max_tokens guards: try max_completion_tokens
        # first, fall back to max_tokens for older providers.
        async def _open_stream():
            primary_kwargs = {}
            if max_tokens is not None:
                primary_kwargs["max_completion_tokens"] = max_tokens
            if reasoning_effort:
                primary_kwargs["reasoning_effort"] = reasoning_effort
            try:
                return await openai_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    stream=True,
                    **primary_kwargs,
                )
            except Exception as primary_err:
                logger.debug(
                    f"[HelperLLM-Stream] max_completion_tokens path failed "
                    f"({primary_err}); retrying with max_tokens"
                )
                fallback_kwargs = (
                    {"max_tokens": max_tokens} if max_tokens is not None else {}
                )
                if reasoning_effort:
                    fallback_kwargs["reasoning_effort"] = reasoning_effort
                return await openai_client.chat.completions.create(
                    model=model_name,
                    messages=messages,
                    stream=True,
                    **fallback_kwargs,
                )

        stream = await _open_stream()
        input_tokens = 0
        output_tokens = 0
        char_count = 0

        async for chunk in stream:
            try:
                choices = chunk.choices or []
            except Exception:
                choices = []
            if not choices:
                continue
            delta = getattr(choices[0].delta, "content", None) or ""
            if delta:
                char_count += len(delta)
                yield delta
            usage = getattr(chunk, "usage", None)
            if usage:
                input_tokens = (
                    getattr(usage, "prompt_tokens", input_tokens) or input_tokens
                )
                output_tokens = (
                    getattr(usage, "completion_tokens", output_tokens)
                    or output_tokens
                )

        logger.info(
            f"[HelperLLM-Stream] completed: model={model_name} "
            f"chars={char_count} input_tokens={input_tokens} "
            f"output_tokens={output_tokens}"
        )

        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db:
            if input_tokens > 0 or output_tokens > 0:
                try:
                    await record_cost(
                        db=_db,
                        agent_id=_agent_id,
                        event_id=None,
                        call_type="llm_stream",
                        model=model_name,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                    )
                except Exception as e:
                    logger.warning(f"[HelperLLM-Stream] failed to record cost: {e}")
            else:
                # No usage chunk arrived (provider lacks stream usage, or the
                # stream was cut before the final usage frame). Don't fail — but
                # don't hide it either.
                warn_missing_usage("HelperLLM-Stream", model_name, "llm_stream")

    async def _try_agents_sdk(
        self, client, model_name, instructions, user_input, output_type,
        max_tokens: Optional[int], reasoning_effort: Optional[str] = None,
    ):
        """Attempt structured output via OpenAI Agents SDK"""
        from agents import Agent, Runner, OpenAIChatCompletionsModel, ModelSettings

        settings_kwargs: dict = {}
        if max_tokens:
            settings_kwargs["max_tokens"] = max_tokens
        if reasoning_effort:
            # Reasoning models (gpt-5*, o-series) accept reasoning.effort
            # via ModelSettings.reasoning. Non-reasoning models will raise
            # — caller controls whether to pass this.
            from openai.types.shared import Reasoning
            settings_kwargs["reasoning"] = Reasoning(effort=reasoning_effort)
        settings = ModelSettings(**settings_kwargs)
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
        reasoning_effort: Optional[str] = None,
    ):
        """
        Direct chat completion with API-level structured output enforcement.

        For ``output_type`` calls, walks a 3-level fallback ladder so the
        most provider-reliable structured-output mechanism is tried first:

          1. ``response_format = json_schema`` (strict). Provider rejects
             anything that does not match the Pydantic schema exactly.
          2. ``response_format = json_object``. Provider guarantees the
             response is parseable JSON; schema validation happens client-
             side via Pydantic.
          3. No ``response_format``. Original prompt-engineering-only path
             — model is asked to return JSON; we regex-extract.

        Per-(base_url, model) capabilities are cached: a level that fails
        with an "unsupported response_format type" error is dropped from
        future attempts. Transient / network errors do NOT downgrade.

        Pre-2026-05-28 this method only did level 3, which made DeepSeek-V4
        responses unreliable (it would happily return JSON-in-prose, the
        regex would mostly work, but inconsistent answers across calls
        polluted downstream judgments like ``ContinuityDetector``).
        """
        # ── 1. Build messages (schema hint goes into the prompt either way —
        # cheap insurance, especially for the level-3 prompt-only path).
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

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        # ── 2. Build base kwargs (max_tokens / reasoning_effort)
        token_kwargs: dict = {}
        if max_tokens is not None:
            token_kwargs["max_completion_tokens"] = max_tokens
        if reasoning_effort:
            token_kwargs["reasoning_effort"] = reasoning_effort

        # ── 3. Single attempt that ALSO handles the max_completion_tokens →
        # max_tokens fallback for older providers. Returns the API response.
        async def _do_call(extra_kwargs: dict):
            try:
                return await client.chat.completions.create(
                    model=model_name, messages=messages,
                    **token_kwargs, **extra_kwargs,
                )
            except Exception as primary_err:
                # If the failure is response_format-shaped, bubble it up
                # so the caller can downgrade levels instead of silently
                # discarding it via the max_tokens fallback.
                if _is_response_format_unsupported_error(primary_err):
                    raise
                # Otherwise, try the legacy max_tokens parameter (some
                # older providers don't accept max_completion_tokens).
                legacy_kwargs = {}
                if max_tokens is not None:
                    legacy_kwargs["max_tokens"] = max_tokens
                if reasoning_effort:
                    legacy_kwargs["reasoning_effort"] = reasoning_effort
                return await client.chat.completions.create(
                    model=model_name, messages=messages,
                    **legacy_kwargs, **extra_kwargs,
                )

        # ── 4. Walk the response_format ladder (skipped entirely when
        # output_type is None — that's the chat-completion-as-plain-text
        # path, no parsing needed).
        resp = None
        chosen_level = "prompt_only"  # default; used for logging only
        if output_type and schema_obj is not None:
            key = _capability_key(model_name)
            allowed = _allowed_levels(key)
            ladder: list[tuple[str, dict]] = []
            if "json_schema" in allowed:
                ladder.append((
                    "json_schema",
                    {"response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": output_type.__name__,
                            "strict": True,
                            "schema": schema_obj,
                        },
                    }},
                ))
            if "json_object" in allowed:
                ladder.append((
                    "json_object",
                    {"response_format": {"type": "json_object"}},
                ))

            for level, extra in ladder:
                try:
                    resp = await _do_call(extra)
                    chosen_level = level
                    break
                except Exception as e:
                    if _is_response_format_unsupported_error(e):
                        _mark_unsupported(key, level)
                        await _audit_framework_downgrade(
                            "response_format_level_unsupported",
                            {
                                "base_url": key[0],
                                "model": key[1],
                                "level": level,
                                "error": str(e)[:500],
                            },
                        )
                        continue  # try next level
                    # Genuine error (rate limit, 5xx, network) — re-raise.
                    raise

        # Level 3 (or non-structured): no response_format flag.
        if resp is None:
            resp = await _do_call({})

        # ── 5. Cost accounting
        raw_content = resp.choices[0].message.content or ""
        _usage = getattr(resp, "usage", None)
        input_tokens = getattr(_usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(_usage, "completion_tokens", 0) or 0
        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db:
            if input_tokens > 0 or output_tokens > 0:
                try:
                    await record_cost(
                        db=_db, agent_id=_agent_id, event_id=None,
                        call_type="llm_function", model=model_name,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                    )
                except Exception as e:
                    logger.warning(f"Failed to record cost: {e}")
            else:
                warn_missing_usage("HelperLLM", model_name, "llm_function")

        if not output_type:
            return _SimpleResult(raw_content, resp)

        # ── 6. Parse JSON. `_extract_json_from_llm_output` strips
        # ```json fences, <think> blocks, and finds the outermost {...}.
        # When level == "json_object" or "json_schema" the API already
        # guarantees JSON, but providers like DeepSeek-V3 still wrap
        # their json_object response in ```json fences, so the extractor
        # stays useful.
        json_str = _extract_json_from_llm_output(raw_content)
        if json_str is None:
            logger.warning(
                f"[StructuredFallback] could not extract JSON via {chosen_level} "
                f"from {model_name}: head={raw_content[:200]!r}"
            )
            raise ValueError(
                f"Could not extract JSON from LLM response (level={chosen_level}, "
                f"model={model_name}): {raw_content[:200]}"
            )

        adapter = TypeAdapter(output_type)
        try:
            parsed = adapter.validate_json(json_str)
        except Exception as e:
            logger.warning(
                f"[StructuredFallback] schema validation failed via "
                f"{chosen_level} on {model_name}: {e!r} json={json_str[:200]!r}"
            )
            raise
        # Stash the chosen level on the contextvar so callers / tests can
        # introspect (currently mostly used by logging-aware timed() tags).
        existing = _last_llm_call_info.get() or {}
        _last_llm_call_info.set({**existing, "response_format": chosen_level})
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
            else:
                warn_missing_usage("HelperLLM-Agents", model_name, "llm_function")
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
