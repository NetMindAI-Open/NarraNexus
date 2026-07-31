"""
@file_name: test_sysprompt_sha.py
@author: NarraNexus
@date: 2026-07-25
@description: R4c instrument calibration — the greppable `sys_sha256=` line
must hash the COMPLETE adapter-facing system prompt (the exact string handed
to the SDK as options.system_prompt = the request's system[2] block).
Experiment E2 (2026-07-25) showed the previous ContextRuntime-level hash
missed adapter-added bytes: the cold-round "=== Chat History ===" tail and
the per-system-message join newline. The canonical hash is now emitted by
the claude adapter post-assemble_argv_prompt via _log_sysprompt_sha.
"""
from __future__ import annotations

import hashlib
import re

from loguru import logger

from xyz_agent_context.agent_framework.adapters.claude.sdk import (
    _log_sysprompt_sha,
)
from xyz_agent_context.agent_framework.adapters.materializer import (
    assemble_argv_prompt,
)


def _capture(fn) -> str:
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        fn()
    finally:
        logger.remove(sink_id)
    return "\n".join(lines)


def _sha12(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:12]


def test_returns_and_logs_sha_over_exact_input_string():
    prompt = "BASE SYSTEM PROMPT\n"
    out = _capture(lambda: _log_sysprompt_sha(prompt, None))
    assert "[SYSPROMPT-SHA]" in out
    m = re.search(r"sys_sha256=([0-9a-f]{12})", out)
    assert m, f"greppable sys_sha256=<12hex> token missing in: {out}"
    assert m.group(1) == _sha12(prompt)
    assert "resume=cold" in out
    assert f"chars={len(prompt)}" in out


def test_resume_flag_rendered_when_session_id_present():
    out = _capture(lambda: _log_sysprompt_sha("P", "sess-abcdef123456"))
    assert "resume=yes" in out


def test_hash_covers_the_cold_round_history_tail():
    """The E2 calibration gap: on cold rounds assemble_argv_prompt appends
    the "=== Chat History ===" tail AFTER ContextRuntime hashed its string.
    Hashing the assembled output must therefore differ from hashing the bare
    prompt — proving the emitted value covers the tail."""
    base = "BASE SYSTEM PROMPT\n"
    history = [
        {"role": "user", "content": "earlier question", "source": "chat"},
        {"role": "assistant", "content": "earlier answer", "source": "chat"},
    ]

    cold = assemble_argv_prompt(base, history)
    resume = assemble_argv_prompt(base, [])

    assert "=== Chat History ===" in cold
    assert "=== Chat History ===" not in resume

    cold_out = _capture(lambda: _log_sysprompt_sha(cold, None))
    resume_out = _capture(lambda: _log_sysprompt_sha(resume, "sess-x"))

    cold_hash = re.search(r"sys_sha256=([0-9a-f]{12})", cold_out).group(1)
    resume_hash = re.search(r"sys_sha256=([0-9a-f]{12})", resume_out).group(1)

    assert cold_hash == _sha12(cold)
    assert resume_hash == _sha12(resume)
    # The tail is inside the hash coverage -> the two rounds differ.
    assert cold_hash != resume_hash


def test_two_resume_rounds_with_stable_prompt_hash_identically():
    """The sentinel semantics: byte-stable prompt across rounds -> same value."""
    p = assemble_argv_prompt("STABLE PROMPT\n", [])
    h1 = re.search(
        r"sys_sha256=([0-9a-f]{12})", _capture(lambda: _log_sysprompt_sha(p, "s1"))
    ).group(1)
    h2 = re.search(
        r"sys_sha256=([0-9a-f]{12})", _capture(lambda: _log_sysprompt_sha(p, "s2"))
    ).group(1)
    assert h1 == h2
