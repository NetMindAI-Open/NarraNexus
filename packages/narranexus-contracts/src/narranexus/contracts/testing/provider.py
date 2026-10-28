"""
@file_name: provider.py
@author: Bin Liang
@date: 2026-09-07
@description: Contract test base for provider driver classes — shape AND behaviour.

Subclass in a test module and set ``driver_cls``; add ``card_factory`` (a
zero-arg callable returning a provider card) to also DRIVE the driver.

The class-shape checks need no credentials and no network, and neither do the
behaviour ones: ``build_*_config`` is pure translation from a card to a config
object, so calling it with a sentinel model and asserting the model comes back
out catches the class of bug the shape checks cannot — a builder that ignores
its ``model`` argument and hands every slot the card's own default, which the
platform then reports as "the model you chose" while a different one runs.
"""
from __future__ import annotations

import inspect
from typing import Any, ClassVar

from narranexus.contracts.provider import ProviderDriver

#: A model id no real catalogue contains, so "the builder echoed MY model" and
#: "the builder fell back to a default" can never look the same.
PROBE_MODEL = "contract-probe-model-1"

_BUILDERS = (
    "build_claude_config",
    "build_openai_config",
    "build_anthropic_helper_config",
    "build_cli_helper_config",
    "build_codex_config",
)


class ProviderDriverContractTests:
    """Executable definition of the ``provider`` contract."""

    driver_cls: ClassVar[type | None] = None
    #: Optional: a zero-arg callable returning a card the driver accepts. Set it
    #: to opt into the behaviour drives below.
    card_factory: ClassVar[Any] = None

    def _cls(self) -> type:
        cls = type(self).driver_cls
        assert cls is not None, "set driver_cls on the subclass"
        return cls

    def test_driver_type_is_a_non_empty_lowercase_key(self):
        key = self._cls().driver_type()
        assert isinstance(key, str) and key and key == key.lower()

    def test_class_exposes_every_contract_method(self):
        for name in ProviderDriver.__protocol_attrs__:  # type: ignore[attr-defined]
            if name == "card":
                continue
            assert hasattr(self._cls(), name), f"missing {name}"

    def test_probe_is_a_coroutine_function(self):
        assert inspect.iscoroutinefunction(self._cls().probe)

    def test_models_is_synchronous(self):
        assert not inspect.iscoroutinefunction(self._cls().models)

    def test_a_built_config_carries_the_model_it_was_asked_for(self):
        """Drives every builder the driver implements. A builder that answers
        ``NotImplementedError`` is declaring "this card cannot fill that slot",
        which is contract-conformant; one that answers a config for a DIFFERENT
        model is the silent substitution this test exists to catch."""
        factory = type(self).card_factory
        if factory is None:
            return
        driver = self._cls()(factory())
        built = 0
        for name in _BUILDERS:
            builder = getattr(driver, name, None)
            if builder is None:
                continue
            try:
                config = builder(PROBE_MODEL)
            except NotImplementedError:
                continue
            assert getattr(config, "model", None) == PROBE_MODEL, (
                f"{name} returned model {getattr(config, 'model', None)!r} instead of the requested "
                f"{PROBE_MODEL!r}"
            )
            built += 1
        assert built, "no builder accepted this card — card_factory must produce one the driver serves"

    def test_models_answers_a_list_of_strings(self):
        factory = type(self).card_factory
        if factory is None:
            return
        models = self._cls()(factory()).models()
        assert isinstance(models, list) and all(isinstance(m, str) for m in models)
