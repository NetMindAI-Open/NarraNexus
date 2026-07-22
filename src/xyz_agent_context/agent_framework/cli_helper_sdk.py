"""
@file_name: cli_helper_sdk.py
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
import os
import tempfile
from typing import AsyncGenerator, Optional, Type

from loguru import logger
from pydantic import BaseModel, TypeAdapter

from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    cli_helper_config,
)
from xyz_agent_context.agent_framework.openai_agents_sdk import (
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
from xyz_agent_context.utils.logging import timed

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
# disposable dir. One shared dir avoids per-call mkdtemp churn. UID-suffixed +
# 0o700 so another local user can't pre-create / read it on a shared host.
_HELPER_CWD = os.path.join(
    tempfile.gettempdir(), f"narranexus-cli-helper-{os.getuid()}"
)


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
    ) -> tuple[str, int, int]:
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
        from xyz_agent_context.agent_framework.model_catalog import resolve_cli_alias

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
            from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
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
        os.makedirs(_HELPER_CWD, mode=0o700, exist_ok=True)
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=resolve_cli_alias(model_name, auth_type=cli_helper_config.auth_type),
            env=env,
            allowed_tools=[],      # pure completion — no tool use
            mcp_servers={},
            max_turns=1,
            cwd=_HELPER_CWD,
        )

        async def _consume() -> tuple[str, int, int]:
            text_parts: list[str] = []
            result_text = ""
            in_tok = out_tok = 0
            async for msg in query(prompt=user_input, options=options):
                if isinstance(msg, AssistantMessage):
                    for block in msg.content:
                        if isinstance(block, TextBlock):
                            text_parts.append(block.text)
                elif isinstance(msg, ResultMessage):
                    result_text = msg.result or ""
                    raw_usage = getattr(msg, "usage", None)
                    if isinstance(raw_usage, dict):
                        in_tok = int(raw_usage.get("input_tokens", 0) or 0)
                        out_tok = int(raw_usage.get("output_tokens", 0) or 0)
            return ("".join(text_parts) or result_text), in_tok, out_tok

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
    ) -> tuple[str, int, int]:
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
        from xyz_agent_context.agent_framework.provider_driver.derive import (
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
    ) -> tuple[str, int, int]:
        from xyz_agent_context.agent_framework import get_agent_loop_driver

        # working_path=_HELPER_CWD: (1) it is a REQUIRED arg for the executor
        # seam (RemoteAgentLoopDriver.__init__) — omitting it TypeErrors once
        # AGENT_EXECUTOR_URL is set; (2) it confines codex's writable_roots /
        # cwd to a disposable per-uid temp dir instead of the backend process
        # cwd, so a prompt-injected helper input (narrative/entity text) can't
        # touch the app tree. The dir must exist before the driver spawns.
        os.makedirs(_HELPER_CWD, mode=0o700, exist_ok=True)
        driver = get_agent_loop_driver(
            framework="codex_cli", working_path=_HELPER_CWD
        )
        # The codex driver derives instructions.md ONLY from role=="system"
        # messages and pops the LAST message as the per-turn user turn. The
        # schema/instructions MUST ride a system message — folding them into
        # the user content leaves instructions.md empty and the codex CLI exits
        # on startup ("model instructions file is empty"), so the codex helper
        # never ran. Mirrors _run_claude_oneshot, which passes system_prompt and
        # user_input separately.
        text_parts: list[str] = []
        in_tok = out_tok = 0
        err_msg = ""
        async for ev in driver.agent_loop(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ],
            mcp_servers={},
        ):
            # The codex driver's translator (output_transfer, codex_official)
            # emits internal events shaped {"type":"raw_response_event",
            # "data":{...}} — the visible assistant text streams as
            # data.type=="response.text.delta" and the terminal usage lands on
            # data.type=="response.done". (An earlier draft read ev["raw_event"]
            # / ev["usage"], keys this translator never sets, so no text was
            # ever accumulated and every structured call failed JSON extraction
            # on an empty body.)
            if not isinstance(ev, dict) or ev.get("type") != "raw_response_event":
                continue
            data = ev.get("data") or {}
            dtype = data.get("type")
            if dtype == "response.text.delta":
                delta = data.get("delta") or ""
                if delta:
                    text_parts.append(delta)
            elif dtype == "response.done":
                usage = data.get("usage")
                if isinstance(usage, dict):
                    in_tok = int(usage.get("input_tokens", in_tok) or in_tok)
                    out_tok = int(usage.get("output_tokens", out_tok) or out_tok)
            elif dtype == "response.error":
                # Codex surfaces auth / quota failures as a terminal error
                # EVENT, not an exception. Capture it so the helper raises a
                # classifiable error below — otherwise the empty text falls
                # through to a misleading "could not extract JSON" on an empty
                # body, masking the real cause (e.g. "unauthorized — re-login")
                # and defeating the #68 credential-failure alerting, which keys
                # off is_credential_error reading the error text.
                # Keep BOTH the type and the message: codex phrases auth
                # failures as error_type="unauthorized" with a message
                # ("access token could not be refreshed …") that carries no
                # credential marker on its own, so dropping the type would make
                # is_credential_error miss it.
                _etype = str(data.get("error_type") or "").strip()
                _emsg = str(data.get("error_message") or "").strip()
                err_msg = ": ".join(p for p in (_etype, _emsg) if p) or "codex error"
        text = "".join(text_parts)
        if not text and err_msg:
            raise RuntimeError(f"codex CLI helper failed: {err_msg}")
        return text, in_tok, out_tok

    async def _run_oneshot(
        self, system_prompt: str, user_input: str, model_name: str
    ) -> tuple[str, int, int]:
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
            raw, in_tok, out_tok = await self._run_oneshot(
                system_prompt, prompt_text, model_name
            )
            if _agent_id and _db:
                if in_tok > 0 or out_tok > 0:
                    try:
                        await record_cost(
                            db=_db, agent_id=_agent_id, event_id=None,
                            call_type="llm_function", model=model_name,
                            input_tokens=in_tok, output_tokens=out_tok,
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
        raw_content, input_tokens, output_tokens = await self._run_oneshot(
            instructions, user_input, model_name
        )
        _agent_id, _db = self._resolve_cost_context(None, None)
        if _agent_id and _db and (input_tokens > 0 or output_tokens > 0):
            try:
                await record_cost(
                    db=_db, agent_id=_agent_id, event_id=None,
                    call_type="llm_stream", model=model_name,
                    input_tokens=input_tokens, output_tokens=output_tokens,
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
