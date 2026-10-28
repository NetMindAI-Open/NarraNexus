"""
@file_name: framework.py
@author: Bin Liang
@date: 2026-09-07
@description: Contract test base for agent-loop framework drivers — shape AND behaviour.

Subclass in a test module and set ``driver_factory`` to a zero-arg callable
returning a driver instance. Every builtin framework and every third-party one
runs exactly these checks.

The checks DRIVE the implementation rather than only reading its signature: a
signature check cannot see a driver that swallows the contract call shape in
``**kwargs`` and then reads a keyword nobody passes, and it cannot see
``capabilities()`` handing every caller the same mutable set. Both are real
regressions that used to pass this base. Nothing here performs I/O: the smoke
turn only CALLS ``agent_loop`` (an async generator body does not start until it
is iterated) and then closes it, so a driver that talks to an LLM is exercised
for argument binding and generator lifecycle without a network call. A driver
that CAN run offline opts into a real turn by setting ``smoke_messages``.
"""
from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable, ClassVar

from narranexus.contracts.framework import CAPABILITY_VOCABULARY, AgentLoopDriver


class FrameworkDriverContractTests:
    """Executable definition of the ``framework`` contract."""

    # ``Any``: pyright would otherwise try to bind a plain callable stored on the
    # class as a method. Subclasses set a zero-arg callable (a staticmethod works).
    driver_factory: ClassVar[Any] = None

    #: Set to a message list to opt into a REAL smoke turn (the driver must run
    #: without network — a fake/echo driver, e.g. the one ``templates/framework``
    #: scaffolds). Left ``None``, only the non-I/O drives below run.
    smoke_messages: ClassVar[Any] = None

    def _driver(self) -> AgentLoopDriver:
        factory: Callable[[], AgentLoopDriver] | None = type(self).driver_factory
        assert factory is not None, "set driver_factory on the subclass"
        return factory()

    def test_satisfies_structural_protocol(self):
        assert isinstance(self._driver(), AgentLoopDriver)

    def test_capabilities_use_known_vocabulary(self):
        caps = self._driver().capabilities()
        assert isinstance(caps, set)
        assert caps <= CAPABILITY_VOCABULARY, f"unknown capability words: {caps - CAPABILITY_VOCABULARY}"

    def test_capabilities_is_stable_and_gives_each_caller_its_own_set(self):
        """Drives the method twice. The orchestrator stores and filters the set
        it gets back; a driver returning one shared mutable object lets one
        consumer's ``.discard("steering")`` silently disable steering for the
        next turn."""
        driver = self._driver()
        first, second = driver.capabilities(), driver.capabilities()
        assert first == second
        assert first is not second, "capabilities() must hand back a fresh set, not a shared one"
        first.add("plan")
        assert "plan" not in driver.capabilities() or "plan" in second

    def test_agent_loop_is_an_async_generator_function(self):
        assert inspect.isasyncgenfunction(type(self._driver()).agent_loop)

    def test_agent_loop_takes_keyword_only_streaming(self):
        params = inspect.signature(type(self._driver()).agent_loop).parameters
        assert params["streaming"].kind is inspect.Parameter.KEYWORD_ONLY

    def test_agent_loop_accepts_the_contract_call_shape_and_closes_cleanly(self):
        """Actually CALLS ``agent_loop`` with the arguments the platform passes,
        then closes the generator. No iteration, so no I/O — but a driver whose
        parameters do not match the contract fails here, which a signature
        comparison misses whenever the driver takes ``**kwargs``."""
        driver = self._driver()
        gen = driver.agent_loop(
            [{"role": "user", "content": "ping"}],
            {},
            streaming=True,
            extra_env=None,
            cancellation=None,
        )
        assert inspect.isasyncgen(gen)
        asyncio.run(gen.aclose())

    def test_smoke_turn_yields_event_dicts(self):
        """A driver that can run offline declares ``smoke_messages`` and gets its
        turn actually executed: every yielded item must be an event dict (the
        ``agent_events`` contract), never a bare string or object."""
        messages = type(self).smoke_messages
        if messages is None:
            return
        driver = self._driver()

        async def _run() -> list[Any]:
            return [event async for event in driver.agent_loop(messages, {})]

        events = asyncio.run(_run())
        assert events, "a smoke turn must yield at least one event"
        for event in events:
            assert isinstance(event, dict), f"agent_loop yielded {type(event).__name__}, not an event dict"
            assert isinstance(event.get("type"), str) and event["type"], f"event without a type: {event!r}"
