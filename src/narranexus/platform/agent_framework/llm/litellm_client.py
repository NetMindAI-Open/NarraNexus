"""
@file_name: litellm_client.py
@author: Bin Liang
@date: 2026-07-29
@description: The repo's single litellm import point — an atomic wrapper
over unified chat completions.

Boundary (iron rule #9): this class does connections and passthrough
only — one streaming chat call, raw chunks out, connection hygiene. It
does NOT translate event semantics (nexus_power's ModelClient does),
classify errors (ErrorClassifier does) or choose models (callers do).
Any other file importing ``litellm`` is an architecture violation
(greppable).

Memory discipline: litellm is imported lazily inside the first call —
processes that never reach a model (tests, tooling) never pay its
footprint.
"""

from __future__ import annotations

from typing import Any, AsyncIterator


class LitellmClient:
    """Stateless thin wrapper; safe as a module-level singleton."""

    def __init__(self, *, default_timeout_s: float = 600.0) -> None:
        self._timeout = default_timeout_s

    async def stream_chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """One streaming chat completion, yielding raw chunk dicts.

        Chunks are passed through without semantic processing; stream
        failures raise the provider's original exception for the
        caller's classifier. ``extra`` is forwarded verbatim (dialect
        content such as thinking parameters enters here).
        """
        litellm = self._litellm()
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "stream_options": {"include_usage": True},
            "timeout": self._timeout,
            "num_retries": 0,  # retry strategy is the loop's concern
        }
        if tools:
            kwargs["tools"] = tools
        if api_key:
            kwargs["api_key"] = api_key
        if base_url:
            kwargs["api_base"] = base_url
        if extra:
            kwargs.update(extra)

        from typing import cast

        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            dump = getattr(chunk, "model_dump", None)
            data = dump() if callable(dump) else chunk
            yield cast(
                dict[str, Any], data if isinstance(data, dict) else vars(data)
            )

    @classmethod
    def model_cost_map(cls) -> dict[str, Any]:
        """litellm's maintained per-model price table, raw.

        Pricing is the other thing callers legitimately need out of
        litellm, and without this they reach for ``import litellm``
        themselves — which is exactly what happened to nexus_power's
        cost estimation and made the "single import point" claim above
        false (2026-07-29 review). The RAW map is returned on purpose:
        which key a model id matches, and how the rates combine, is
        semantics and belongs to the caller. This class stays
        connections-and-passthrough.
        """
        return getattr(cls._litellm(), "model_cost", None) or {}

    @staticmethod
    def _litellm() -> Any:
        """Lazy import + one-time quietening (idempotent)."""
        import litellm

        litellm.drop_params = True          # unknown params never hard-fail a dialect
        litellm.suppress_debug_info = True
        return litellm
