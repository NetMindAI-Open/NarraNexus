"""
@file_name: memory.py
@author: Bin Liang
@date: 2026-09-07
@description: Contract test base for memory-kind specs — shape AND behaviour.

Subclass in a test module and set ``spec_factory`` to a zero-arg callable
returning the spec object.

Beyond the structural minimum, the base DRIVES the spec: ``passive`` is the
field the write path reads to decide whether a kind is recorded by the platform
or only ever read back, so the base asserts the spec answers the same value
twice (a property computing it from mutable global state is a real bug) and
that a PASSIVE kind really refuses a write when the spec exposes a write hook.
Reading a spec's fields is free — no database, no LLM.
"""
from __future__ import annotations

from typing import Any, Callable, ClassVar

from narranexus.contracts.memory import MemoryKindContract


class MemoryKindContractTests:
    """Executable definition of the ``memory`` contract."""

    # ``Any`` for the same reason as FrameworkDriverContractTests.driver_factory.
    spec_factory: ClassVar[Any] = None

    def _spec(self) -> MemoryKindContract:
        factory: Callable[[], MemoryKindContract] | None = type(self).spec_factory
        assert factory is not None, "set spec_factory on the subclass"
        return factory()

    def test_satisfies_structural_protocol(self):
        assert isinstance(self._spec(), MemoryKindContract)

    def test_kind_is_a_non_empty_identifier(self):
        kind = self._spec().kind
        assert isinstance(kind, str) and kind.isidentifier()

    def test_passive_is_a_bool(self):
        assert isinstance(self._spec().passive, bool)

    def test_the_spec_answers_the_same_facts_every_time(self):
        """Drives the fields twice on two freshly built specs: a kind whose
        ``kind``/``passive`` depends on ambient state (a setting read at
        property time, a registry lookup) makes "is this kind recorded?" differ
        between the writer and the reader in the same process."""
        first, second = self._spec(), self._spec()
        assert first.kind == second.kind
        assert first.passive == second.passive

    def test_a_passive_kind_does_not_offer_a_write_path(self):
        """``passive`` means the platform never records this kind itself — it is
        projected from rows someone else owns. A spec that says passive AND
        exposes a writer is contradictory, and the write path trusts the flag."""
        spec = self._spec()
        if not spec.passive:
            return
        for hook in ("write", "record", "store"):
            assert getattr(spec, hook, None) is None, f"passive kind {spec.kind!r} exposes {hook}()"
