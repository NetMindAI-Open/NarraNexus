"""
@file_name: llm_client.py
@author: Bin Liang
@date: 2026-09-07
@description: Contract test base for helper-LLM clients — shape AND behaviour.

Subclass in a test module and set ``client_cls``. The signature checks compare
``llm_function`` / ``llm_stream`` with the ``LlmClient`` Protocol so a drifted
parameter name fails here, not at a call site three layers away.

The behaviour check drives the one piece of a helper client that is pure and
therefore testable without a key or a network: MODEL RESOLUTION. Every client
must route "the slot's configured model" and "this call site's preference"
through ``resolve_helper_model``; a client that quietly prefers its own
hardcoded default sends a different model than the settings page shows, and no
signature comparison can see that. A client whose resolution is not reachable
without I/O opts out by leaving ``model_resolver`` unset.
"""
from __future__ import annotations

import inspect
from typing import Any, ClassVar

from narranexus.contracts.llm_client import DEFAULT_MODEL_SENTINEL, LlmClient


def _param_names(fn) -> list[str]:
    return [p for p in inspect.signature(fn).parameters if p != "self"]


class LlmClientContractTests:
    """Executable definition of the ``llm_client`` contract."""

    client_cls: ClassVar[type | None] = None
    #: Optional: a callable ``(slot_model, requested_model) -> str`` exposing the
    #: client's own model resolution (usually a staticmethod on the class).
    model_resolver: ClassVar[Any] = None

    def _cls(self) -> type:
        cls = type(self).client_cls
        assert cls is not None, "set client_cls on the subclass"
        return cls

    def test_llm_function_is_a_coroutine_with_the_contract_parameters(self):
        fn = self._cls().llm_function
        assert inspect.iscoroutinefunction(fn)
        assert _param_names(fn) == _param_names(LlmClient.llm_function)

    def test_llm_stream_is_an_async_generator_with_the_contract_parameters(self):
        fn = self._cls().llm_stream
        assert inspect.isasyncgenfunction(fn)
        assert _param_names(fn) == _param_names(LlmClient.llm_stream)

    def test_class_satisfies_the_structural_protocol(self):
        assert all(hasattr(self._cls(), name) for name in ("llm_function", "llm_stream"))

    def test_model_resolution_prefers_the_configured_slot_model(self):
        resolver = type(self).model_resolver
        if resolver is None:
            return
        assert resolver("configured-model", None) == "configured-model"
        # The "use the system preset" sentinel must NOT be sent as a model id.
        assert resolver(DEFAULT_MODEL_SENTINEL, None) != DEFAULT_MODEL_SENTINEL
        assert resolver(None, None)
