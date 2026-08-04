"""
@file_name: cli_helper.py
@author:
@date: 2026-07-07
@description: CLI-backed helper_llm caller (subscription / OAuth helper).

When the helper_llm slot points at a subscription provider — Claude Code
(``claude_oauth``) or Codex (``codex_oauth``) — the OAuth credential cannot
make direct Messages / Chat-Completions API calls, so the helper's small
structured-output calls run through the SAME CLI the subscription authorizes.
This is what lets a single subscription login cover BOTH the agent slot and
the helper_llm slot with no separate API key.

Interface-compatible with OpenAIAgentsSDK / AnthropicHelperSDK
(``llm_function`` / ``llm_stream``) so the ~15 helper call sites work unchanged
through ``get_helper_sdk()``. Call sites never import this class directly.

Two backends, chosen by ``cli_helper_config.framework``:
  - "claude_code": one-shot ``claude_agent_sdk.query()`` (tool-free, single
    turn), reusing the same ``ClaudeConfig.to_cli_env`` credential wiring the
    agent loop uses.
  - "codex_cli": the registered codex agent-loop driver in a one-shot,
    reusing the ambient ``codex_config`` (already set to the subscription for
    a codex-agent user) and its tested CODEX_HOME / credential staging.

Structured output uses the same prompt-engineered path as AnthropicHelperSDK
(schema embedded in the system prompt, JSON extracted + validated
client-side), reusing its extractor and result wrappers so downstream
consumers see identical shapes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import AsyncGenerator, Optional, Type

from loguru import logger
from pydantic import BaseModel, TypeAdapter

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    cli_helper_config,
)
from xyz_agent_context.agent_framework.anthropic_usage import (
    normalize_anthropic_usage,
)
from xyz_agent_context.agent_framework.adapters.openai_agents import (
    _ParsedResult,
    _SimpleResult,
    _extract_json_from_llm_output,
    _last_llm_call_info,
    json_repair_note,
)
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.cost_tracker import (
    get_cost_context,
    record_cost,
    warn_missing_usage,
)
from xyz_agent_context.agent_framework.llm._prompt_probe import emit as _probe_emit
from xyz_agent_context.utils.logging import timed


@dataclass(frozen=True)
class HelperUsage:
    """Token usage from one CLI one-shot, buckets kept apart.

    Replaces the ``(text, in_tok, out_tok)`` tuple this module used to return.
    That shape had no room for the prompt-cache counters, so a CLI one-shot —
    running the very transport whose agent_loop sibling reports six-figure
    cache_read counts — booked every call as if none of it was cached.

    ``input_tokens`` is the FULL-RATE bucket only. Anthropic's three counters
    are mutually exclusive and priced 1x / 1.25x / 0.1x; the codex branch fills
    only the first because the OpenAI shape folds cached input into its input
    total (see its return site).
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0

    @property
    def any_recorded(self) -> bool:
        """True when the provider reported ANY usage at all.

        Cache buckets count: a fully cache-hit call can legitimately report
        input_tokens == 0 while still costing money, and treating that as
        "no usage reported" would drop a real row and fire a bogus warning.
        """
        return bool(
            self.input_tokens
            or self.output_tokens
            or self.cache_creation_tokens
            or self.cache_read_tokens
        )


_EMPTY_USAGE = HelperUsage()

# Cheap sensible defaults per framework when the slot model is empty/"default".
# Codex NOTE: a subscription runs Codex against a ChatGPT ACCOUNT, which rejects
# the API-key-only "-codex-mini" model ids (400 "not supported when using Codex
# with a ChatGPT account" — verified live 2026-07-08). Use a plain gpt-5.x id;
# gpt-5.4-mini is accepted and is also the openai helper onboard default.
_DEFAULT_CLAUDE_HELPER_MODEL = "haiku"
_DEFAULT_CODEX_HELPER_MODEL = "gpt-5.4-mini"

# Neutral cwd / sandbox root for the CLI one-shots — the claude branch is
# tool-free (allowed_tools=[]) and the codex branch points its writable_roots
# here (never the backend cwd), so any codex file op is confined to this
# disposable dir. Provisioned via cli_oneshot.oneshot_cwd, which also
# verifies st_uid ownership before reuse (a same-named dir pre-created by
# another user on a shared host must not be silently adopted).
def _helper_cwd() -> str:
    from xyz_agent_context.agent_framework.llm.cli_oneshot import oneshot_cwd

    return oneshot_cwd("cli-helper")


class CliHelperSDK:
    """Helper-LLM client that runs one-shot completions through a coding CLI."""

    @staticmethod
    def _resolve_model(requested_model: Optional[str]) -> str:
        """Slot model wins; per-call-site ``model=`` overrides are ignored.

        Call sites pass OpenAI-flavoured model names (e.g. the narrative
        judge's gpt-5.4-mini) that don't exist on a Claude/Codex subscription.
        Fall back to the framework's cheap default when the slot model is
        empty or the "default" sentinel.
        """
        slot_model = cli_helper_config.model
        if slot_model and slot_model != "default":
            return slot_model
        if cli_helper_config.framework == "codex_cli":
            return _DEFAULT_CODEX_HELPER_MODEL
        return _DEFAULT_CLAUDE_HELPER_MODEL

    @staticmethod
    def _build_system_prompt(instructions: str, output_type: Optional[Type[BaseModel]]) -> str:
        if not output_type:
            return instructions
        schema_obj = output_type.model_json_schema()
        return instructions + (
            "\n\nYou MUST respond with ONLY a valid JSON object matching "
            "this schema. No markdown, no code blocks, no explanation, "
            "no <think> tags. ONLY the raw JSON object.\n"
            f"Schema: {json.dumps(schema_obj, ensure_ascii=False)}"
        )

    async def _run_claude_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, HelperUsage]:
        """One-shot, tool-free ``claude_agent_sdk.query()``.

        Reuses ClaudeConfig.to_cli_env so an OAuth subscription's blank
        api_key makes the CLI read ~/.claude credentials, exactly like the
        agent loop. Returns (text, input_tokens, output_tokens).
        """
        from claude_agent_sdk import (
            AssistantMessage,
            ClaudeAgentOptions,
            ResultMessage,
            TextBlock,
            query,
        )
        from xyz_agent_context.agent_framework.providers.model_catalog import resolve_cli_alias

        cfg = ClaudeConfig(
            api_key=cli_helper_config.api_key,
            base_url=cli_helper_config.base_url,
            model=model_name,
            auth_type=cli_helper_config.auth_type,
        )
        env = cfg.to_cli_env()
        # Bound the helper subprocess. to_cli_env injects the AGENT-LOOP retry
        # budget (API_TIMEOUT_MS=llm_api_timeout_ms ≈ 10 min/request ×
        # CLAUDE_CODE_MAX_RETRIES=llm_max_retries), which for a one-shot helper
        # extraction means a bad/hijacked endpoint could hang ~100 min — the
        # "Job stuck at 正在创建" symptom when helper_llm was set to Claude.
        # A helper one-shot is NOT the agent_loop (single turn, tool-free), so
        # bounding it does not violate 铁律 #14.
        env["API_TIMEOUT_MS"] = str(settings.helper_cli_timeout_ms)
        env["CLAUDE_CODE_MAX_RETRIES"] = str(settings.helper_cli_max_retries)
        # OAuth helper runs against the isolated CLAUDE_CONFIG_DIR that to_cli_env
        # set (#76). Stage the credential into it ourselves so the helper is
        # self-sufficient — it must work even when the agent slot is NOT claude
        # (codex agent + claude helper) or when a background-only hook fires with
        # no prior claude agent_loop to seed the shared dir. Same stager the
        # agent loop uses (macOS Keychain export included).
        if cli_helper_config.auth_type == "oauth":
            from xyz_agent_context.agent_framework.adapters.claude.sdk import (
                _stage_claude_oauth_credentials,
            )
            _cfg_dir = env.get("CLAUDE_CONFIG_DIR")
            if _cfg_dir:
                _stage_claude_oauth_credentials(_cfg_dir)
        # Observability (#1): log the provider the subprocess will ACTUALLY use,
        # so a personal ~/.claude/settings.json hijack (base_url redirected off
        # the configured provider) is greppable instead of a silent black box.
        logger.info(
            f"[CliHelper] subprocess provider (effective): "
            f"base_url={env.get('ANTHROPIC_BASE_URL') or '(official)'}, "
            f"auth={'token' if env.get('ANTHROPIC_AUTH_TOKEN') else ('key' if env.get('ANTHROPIC_API_KEY') else 'none')}, "
            f"config_dir={env.get('CLAUDE_CONFIG_DIR')}"
        )
        helper_cwd = _helper_cwd()
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=resolve_cli_alias(model_name, auth_type=cli_helper_config.auth_type),
            env=env,
            allowed_tools=[],      # pure completion — no tool use
            mcp_servers={},
            max_turns=1,
            cwd=helper_cwd,
        )

        async def _consume() -> tuple[str, HelperUsage]:
            text_parts: list[str] = []
            result_text = ""
            # The CLI reports Anthropic-shaped usage, so the three buckets stay
            # apart all the way to the ledger — they are priced 1x / 1.25x /
            # 0.1x and this transport DOES get cache hits (agent_loop runs the
            # same CLI and reports large cache_read counts).
            tally = _EMPTY_USAGE
            async for msg in query(prompt=user_input, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(msg, ResultMessage):
                    result_text = msg.result or ""
                    raw_usage = getattr(msg, "usage", None)
                    if isinstance(raw_usage, dict):
                        usage = normalize_anthropic_usage(raw_usage)
                        tally = HelperUsage(
                            input_tokens=usage["uncached_input_tokens"],
                            output_tokens=usage["output_tokens"],
                            cache_creation_tokens=usage["cache_creation_input_tokens"],
                            cache_read_tokens=usage["cache_read_input_tokens"],
                        )
            return ("".join(text_parts) or result_text), tally

        # Wall-clock bound for the whole one-shot (all internal CLI retries). On
        # timeout wait_for cancels the coroutine; claude_agent_sdk's
        # process_query tears the subprocess down in its own try/finally
        # (await query.close()) as the cancellation propagates. Raises a
        # classifiable error so the caller surfaces it (never an infinite
        # "创建中").
        try:
            return await asyncio.wait_for(
                _consume(), timeout=settings.helper_cli_total_timeout_seconds
            )
        except asyncio.TimeoutError as e:
            raise TimeoutError(
                f"CLI helper one-shot exceeded "
                f"{settings.helper_cli_total_timeout_seconds}s "
                f"(model={model_name}, base_url={env.get('ANTHROPIC_BASE_URL') or '(official)'})"
            ) from e

    async def _run_codex_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, HelperUsage]:
        """One-shot via the registered codex agent-loop driver.

        The codex driver reads model / credentials from the ambient
        ``codex_config`` ContextVar — which is the AGENT slot's config, NOT the
        helper's. So we install a CodexConfig built from THIS helper's
        ``cli_helper_config`` (its own slot model + OAuth credential ref) for the
        duration and reset after, mirroring how ``_run_claude_oneshot`` builds
        its own ClaudeConfig. Without this the codex helper (a) runs the agent's
        model instead of its cheap slot model, and (b) has no credentials at all
        when the agent slot is NOT codex (e.g. claude agent + codex helper) →
        empty CODEX_HOME → unauthorized. Best-effort JSON: codex is agentic, so
        reliability rests on the schema prompt + extractor.
        """
        from xyz_agent_context.agent_framework.api_config import (
            CodexConfig,
            _codex_ctx,
        )
        from xyz_agent_context.agent_framework.providers.driver.derive import (
            CODEX_CLI_CREDENTIALS_REF,
        )

        # Run codex on the HELPER's own model + credentials, not the agent's.
        _auth_type = cli_helper_config.auth_type or "oauth"
        _helper_codex = CodexConfig(
            api_key=cli_helper_config.api_key or "",
            base_url=cli_helper_config.base_url or "",
            model=model_name,
            auth_type=_auth_type,
            # OAuth stages ~/.codex/auth.json via this ref (to_cli_env /
            # _stage_codex_oauth_credentials read it); api-key codex uses the key.
            auth_ref=(CODEX_CLI_CREDENTIALS_REF if _auth_type == "oauth" else ""),
        )
        _codex_token = _codex_ctx.set(_helper_codex)
        try:
            return await self._run_codex_oneshot_inner(system_prompt, user_input)
        finally:
            _codex_ctx.reset(_codex_token)

    async def _run_codex_oneshot_inner(
        self, system_prompt: str, user_input: str
    ) -> tuple[str, HelperUsage]:
        from xyz_agent_context.agent_framework.llm.cli_oneshot import (
            run_codex_cli_oneshot,
        )

        # The codex driver derives instructions.md ONLY from role=="system"
        # messages and pops the LAST message as the per-turn user turn — the
        # shared runner passes system_prompt/user_input as separate messages
        # for exactly that reason (mirrors _run_claude_oneshot).
        #
        # working_path=_helper_cwd() keeps the helper's artifacts in ITS
        # namespace (see the _helper_cwd comment); driver construction, event
        # parsing and the error-event contract live in cli_oneshot (shared
        # with CodexOAuthDriver.verify_live since the PR #224 review).
        result = await run_codex_cli_oneshot(
            system_prompt, user_input, working_path=_helper_cwd()
        )
        if not result.text and result.error:
            # Raise a classifiable error — otherwise the empty text falls
            # through to a misleading "could not extract JSON" on an empty
            # body, masking the real cause (e.g. "unauthorized — re-login")
            # and defeating the #68 credential-failure alerting, which keys
            # off is_credential_error reading the error text.
            raise RuntimeError(f"codex CLI helper failed: {result.error}")
        # Codex speaks the OpenAI usage shape, where the input total already
        # INCLUDES cached input and the cached count is only a breakdown. There
        # is no separate bucket to split out, so the cache fields stay 0 —
        # deliberately, not by omission. CliOneshotResult carries no cache
        # fields for the same reason.
        return result.text, HelperUsage(
            input_tokens=result.input_tokens, output_tokens=result.output_tokens
        )

    async def _run_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, HelperUsage]:
        if cli_helper_config.framework == "codex_cli":
            return await self._run_codex_oneshot(system_prompt, user_input, model_name)
        return await self._run_claude_oneshot(system_prompt, user_input, model_name)

    @timed("llm.cli_helper.llm_function", slow_threshold_ms=15000)
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
        """Run a one-shot helper completion through the subscription's CLI.

        ``reasoning_effort`` is accepted for interface parity and ignored (the
        one-shot CLI path has no per-call knob; the platform never errors on a
        user's parameter choice — iron rule #15).
        """
        model_name = self._resolve_model(model)
        _probe_emit("cli", model_name, instructions, user_input)
        framework = cli_helper_config.framework
        system_prompt = self._build_system_prompt(instructions, output_type)
        logger.debug(
            f"[CliHelper] one-shot: framework={framework} model={model_name} "
            f"output_type={output_type.__name__ if output_type else 'None'}"
        )

        _agent_id, _db = self._resolve_cost_context(agent_id, db)

        async def _call_and_record(prompt_text: str) -> str:
            """One CLI one-shot + per-attempt cost accounting.

            OAuth subscription calls may report zero tokens (the CLI bills the
            subscription, not us); record when present, warn (not error) when
            absent. Each repair attempt is a distinct call, so cost is recorded
            per attempt.
            """
            raw, usage = await self._run_oneshot(
                system_prompt, prompt_text, model_name
            )
            if _agent_id and _db:
                if usage.any_recorded:
                    try:
                        await record_cost(
                            db=_db, agent_id=_agent_id, event_id=None,
                            call_type="llm_function", model=model_name,
                            input_tokens=usage.input_tokens,
                            output_tokens=usage.output_tokens,
                            cache_creation_tokens=usage.cache_creation_tokens,
                            cache_read_tokens=usage.cache_read_tokens,
                        )
                    except Exception as e:
                        logger.warning(f"[CliHelper] failed to record cost: {e}")
                else:
                    warn_missing_usage("CliHelper", model_name, "llm_function")
            return raw

        if not output_type:
            raw_content = await _call_and_record(user_input)
            _last_llm_call_info.set({"model": model_name, "structured": "cli_no_schema"})
            return _SimpleResult(raw_content, None)

        # Prompt-engineered structured output: extract + validate, and on
        # failure re-prompt for valid JSON up to helper_json_repair_attempts
        # times (see json_repair_note). Complex nested schemas on the CLI
        # one-shot path (esp. Haiku) sometimes return prose / schema-divergent
        # JSON on the first try; a single throw there silently dropped the
        # caller's intent (e.g. an Instance-Decision job never got created).
        adapter = TypeAdapter(output_type)
        attempts = max(1, settings.helper_json_repair_attempts)
        prompt_text = user_input
        last_reason = ""
        raw_content = ""
        for attempt in range(1, attempts + 1):
            raw_content = await _call_and_record(prompt_text)
            json_str = _extract_json_from_llm_output(raw_content)
            if json_str is not None:
                try:
                    parsed = adapter.validate_json(json_str)
                    _last_llm_call_info.set(
                        {"model": model_name, "structured": "cli_prompt"}
                    )
                    return _ParsedResult(parsed, raw_content, None)
                except Exception as e:
                    last_reason = f"schema validation failed: {e}"
            else:
                last_reason = "no JSON object found in the response"
            logger.warning(
                f"[CliHelper] {framework}/{model_name} attempt {attempt}/{attempts}: "
                f"{last_reason}; head={raw_content[:200]!r}"
            )
            if attempt < attempts:
                # Each CLI one-shot is a fresh, stateless subprocess (max_turns=1,
                # query() re-spawns), so the model never sees its "previous
                # response" — feed the bad reply back inline, otherwise
                # json_repair_note's reference dangles and only the generic
                # "output only JSON" hint survives. This is exactly the
                # CLI-helper + Haiku path the Lark bug reported.
                prompt_text = (
                    f"{user_input}\n\nYour previous response was:\n"
                    f"{raw_content[:4000]}" + json_repair_note(last_reason)
                )

        raise ValueError(
            f"CLI helper did not return schema-valid JSON after {attempts} "
            f"attempts (framework={framework}, model={model_name}): "
            f"{last_reason}; last head={raw_content[:200]}"
        )

    @timed("llm.cli_helper.llm_stream", slow_threshold_ms=15000)
    async def llm_stream(
        self,
        instructions: str,
        user_input: str,
        model: str = None,
        reasoning_effort: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Stream a plain-text helper reply.

        The one-shot CLI path has no incremental stream we can forward here,
        so we run the completion and yield the full text once. Matches the
        OpenAI/Anthropic helper interface for the fallback-reply path.
        """
        model_name = self._resolve_model(model)
        _last_llm_call_info.set({"model": model_name, "structured": "cli_stream"})
        raw_content, usage = await self._run_oneshot(
            instructions, user_input, model_name
        )
        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db and usage.any_recorded:
            try:
                await record_cost(
                    db=_db, agent_id=_agent_id, event_id=None,
                    call_type="llm_stream", model=model_name,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_tokens=usage.cache_creation_tokens,
                    cache_read_tokens=usage.cache_read_tokens,
                )
            except Exception as e:
                logger.warning(f"[CliHelper-Stream] failed to record cost: {e}")
        if raw_content:
            yield raw_content

    def _resolve_cost_context(self, agent_id, db):
        _agent_id, _db = agent_id, db
        if not _agent_id or not _db:
            ctx = get_cost_context()
            if ctx:
                _agent_id, _db = ctx
        return _agent_id, _db
