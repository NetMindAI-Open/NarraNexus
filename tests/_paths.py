"""
@file_name: _paths.py
@author: Bin Liang
@date: 2026-09-07
@description: Shared source-root resolution for "scan the engine" guard tests.

Batch 6 moved most channel/module/bundle code out of ``src/narranexus/platform``
into ``plugins/<id>/src/narranexus_plugins/`` (and a few shared packages into
``packages/<id>/src``). Several guard tests that scan the filesystem for a
class/pattern (rather than asserting on runtime behaviour) hard-coded the old
``src/`` — only root. When the guarded code moved and the scan root did not,
the loop body ran over zero matching files and the test kept passing for the
wrong reason: it found nothing to complain about because it looked nowhere.

``engine_source_roots()`` is the single place these guards should get "every
directory that can contain first-party engine code" from, so the next time a
package moves, fixing this file fixes every guard at once instead of each
guard silently going blind on its own schedule.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def engine_source_roots() -> list[Path]:
    """All first-party source roots: the platform package, backend, every
    builtin plugin's ``src/``, and every shared package's ``src/``.

    Excludes ``tests/`` directories and ``__pycache__`` by construction (it
    only returns root directories to ``rglob``/``glob`` from; callers must
    still skip ``__pycache__`` and any ``tests`` subtree themselves when they
    walk these roots, since a root's own subtree can contain both).
    """
    roots = [REPO_ROOT / "src", REPO_ROOT / "backend"]
    roots.extend(sorted((REPO_ROOT / "plugins").glob("*/src")))
    roots.extend(sorted((REPO_ROOT / "packages").glob("*/src")))
    return [r for r in roots if r.is_dir()]


def iter_engine_py_files():
    """Yield every ``*.py`` file under :func:`engine_source_roots`, excluding
    ``__pycache__`` and any ``tests``/``__tests__`` subdirectory."""
    for root in engine_source_roots():
        for path in root.rglob("*.py"):
            parts = path.parts
            if "__pycache__" in parts:
                continue
            if "tests" in parts or "__tests__" in parts:
                continue
            yield path
