"""
@file_name: bisect.py
@author: Bin Liang
@date: 2026-09-03
@description: Halving search for the plugin that breaks the app, persisted in ``registry.json`` (spec §9.5).

After safe mode kicked in, the user starts a bisect: half of the suspected
plugins are enabled, the user answers "good" or "bad", and the suspect set
halves each round — O(log N) restarts instead of N. State lives in the
registry file so it survives the restarts it requires; the only mutations
are the enabled flags of user plugins and the ``bisect`` field.
"""
from __future__ import annotations

from dataclasses import dataclass

from narranexus.kernel.plugins.lifecycle import BisectState, RegistryError, RegistryFile, RegistryStore


@dataclass(frozen=True)
class BisectStep:
    trial: tuple[str, ...]  # enabled for the next boot
    remaining: int  # suspects still in play
    culprit: str | None = None


class Bisect:
    def __init__(self, store: RegistryStore) -> None:
        self._store = store

    def start(self) -> BisectStep:
        def _mutate(reg: RegistryFile) -> None:
            suspects = sorted(pid for pid, rec in reg.plugins.items() if rec.enabled or rec.state == "disabled")
            if len(suspects) < 2:
                raise RegistryError("bisect needs at least two user plugins")
            reg.bisect = BisectState(candidates=suspects, trial=suspects[: len(suspects) // 2], cleared=[])
            self._apply_trial(reg)

        reg = self._store.update(_mutate)
        assert reg.bisect is not None
        return BisectStep(tuple(reg.bisect.trial), len(reg.bisect.candidates))

    def _apply_trial(self, reg: RegistryFile) -> None:
        assert reg.bisect is not None
        trial = set(reg.bisect.trial)
        for pid in reg.bisect.candidates:
            rec = reg.plugins[pid]
            rec.enabled = pid in trial
            rec.state = "registered" if rec.enabled else "disabled"
        for pid in reg.bisect.cleared:
            reg.plugins[pid].enabled = False
            reg.plugins[pid].state = "disabled"

    def answer(self, good: bool) -> BisectStep:
        """The user booted with the trial half enabled: ``good`` means the culprit is in the OTHER half."""

        def _mutate(reg: RegistryFile) -> None:
            if reg.bisect is None:
                raise RegistryError("no bisect in progress")
            b = reg.bisect
            trial = set(b.trial)
            if good:
                # the trial half booted fine: they are cleared, the culprit is in the rest
                b.cleared = sorted(set(b.cleared) | trial)
                b.candidates = [pid for pid in b.candidates if pid not in trial]
            else:
                # the trial half is guilty: the rest is exonerated
                b.cleared = sorted(set(b.cleared) | {pid for pid in b.candidates if pid not in trial})
                b.candidates = [pid for pid in b.candidates if pid in trial]
            if len(b.candidates) == 1:
                b.trial = []
                self._apply_trial(reg)
                return
            b.trial = b.candidates[: len(b.candidates) // 2]
            self._apply_trial(reg)

        reg = self._store.update(_mutate)
        assert reg.bisect is not None
        b = reg.bisect
        culprit = b.candidates[0] if len(b.candidates) == 1 else None
        return BisectStep(tuple(b.trial), len(b.candidates), culprit)

    def stop(self, *, re_enable_cleared: bool = True) -> None:
        """End the search: cleared plugins come back; the culprit (if found) stays disabled."""

        def _mutate(reg: RegistryFile) -> None:
            b = reg.bisect
            if b is None:
                return
            culprit = b.candidates[0] if len(b.candidates) == 1 else None
            for pid in b.cleared + [c for c in b.candidates if c != culprit]:
                if re_enable_cleared and pid in reg.plugins:
                    reg.plugins[pid].enabled = True
                    reg.plugins[pid].state = "registered"
            if culprit and culprit in reg.plugins:
                reg.plugins[culprit].enabled = False
                reg.plugins[culprit].state = "disabled"
                reg.plugins[culprit].warnings.append("identified by bisect as the plugin that breaks startup")
            reg.bisect = None

        self._store.update(_mutate)

    def state(self) -> BisectState | None:
        return self._store.read().bisect


__all__ = ["Bisect", "BisectStep"]
