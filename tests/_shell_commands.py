"""
@file_name: _shell_commands.py
@author: NarraNexus
@date: 2026-09-10
@description: Shared "which lines of this shell/Dockerfile actually RUN a
              command" parser for the guard tests that read build scripts.

Lives at the tests/ root rather than under tests/release/ because both of its
consumers are not in one domain: a tests/backend/ guard importing a private
tests/release/ helper reads like a layering accident.

Two guards assert on the same file (scripts/release/build-desktop.sh) from
different angles — tests/backend/test_plugins_extra_lockstep.py (the install
stays uv-driven and light) and tests/release/test_desktop_build_uses_lock.py
(the install comes from the lock, and the bundle gets imported). They each grew
their own copy of the "fold continuations, drop comments and echo'd mentions"
logic, and the 2026-09-10 change to step 3 immediately had to patch both. One
implementation, so the next reshape of that step cannot leave one guard reading
the file correctly and the other quietly matching nothing.
"""
from __future__ import annotations

import re
from pathlib import Path


def command_lines(path: Path, *needles: str) -> list[str]:
    """Logical lines in ``path`` that RUN one of ``needles``.

    Backslash continuations are folded so a multi-line command is judged as
    one; comments and lines that merely `echo` the needle are dropped, because
    a guard that matches its own error message is a guard that never fails.
    """
    text = re.sub(r"\\\n", " ", path.read_text(encoding="utf-8", errors="replace"))
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        # `echo`/`printf` only. A line STARTING with a quote used to be dropped
        # too (it looked like the continuation of an echo'd string), but
        # continuations are already folded above, so all that rule did was hide
        # real commands whose executable is a quoted path — e.g.
        # `"$PYTHON_DIR/bin/python3" scripts/release/bundle_import_smoke.py`.
        if stripped.startswith(("echo", "printf", "@echo")):
            continue
        if any(needle in stripped for needle in needles):
            out.append(" ".join(stripped.split()))
    return out
