"""
@file_name: test_system_prompt_breakdown.py
@author: Hongyi Gu
@date: 2026-07-14
@description: Unit tests for ContextRuntime._log_system_prompt_breakdown — the
[SYSPROMPT-BREAKDOWN] diagnostic added for the system-prompt-growth incident
(2026-07). Verifies the one-line breakdown reports every Part's byte size, all
module-instruction contributors, and the Narrative's growth-prone sub-fields
(current_summary chars, dynamic_summary entry count).

R4d (2026-07-28) changed two things about this line, both to make SAME-LENGTH
divergence visible — the class of prefix breaker that byte counts cannot see:
- modules are printed in EMITTED order (the order the blocks were
  concatenated), not sorted by size descending. Under size-desc sort a block
  REORDER printed identically, so the diagnostic actively hid it.
- prefix-bucket hashes (pfx2k / pfx8k / pfx32k) localize a divergence to a
  region of the prompt without a dump or a packet capture.
"""
from loguru import logger

from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.schema.module_schema import ModuleInstructions


def _mi(name: str, size: int, priority: int = 5) -> ModuleInstructions:
    return ModuleInstructions(name=name, instruction="x" * size, priority=priority)


def _capture(fn) -> str:
    lines: list[str] = []
    sink_id = logger.add(lambda m: lines.append(str(m)), level="INFO")
    try:
        fn()
    finally:
        logger.remove(sink_id)
    return "\n".join(lines)


def test_breakdown_reports_each_part_and_total():
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="agent_abc",
        total_chars=98575,
        part_sizes={"security": 100, "temporal": 200, "narrative": 12000, "modules": 70000, "bootstrap": 0},
        module_instructions_list=[_mi("ChatModule", 5000, 1)],
        narrative_meta={"nar_summary_chars": 9000, "nar_dynamic_entries": 42},
    ))
    assert "[SYSPROMPT-BREAKDOWN]" in out
    assert "agent=agent_abc" in out
    assert "total=98575" in out
    # every Part is named even when zero (bootstrap=0), so growth is greppable
    for token in ("security=100", "temporal=200", "narrative=12000", "modules=70000", "bootstrap=0"):
        assert token in out, token


def test_breakdown_surfaces_narrative_growth_fields():
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="a",
        total_chars=1,
        part_sizes={},
        module_instructions_list=[],
        narrative_meta={"nar_summary_chars": 9000, "nar_dynamic_entries": 42},
    ))
    # the prime growth suspects must be individually measurable per round
    assert "nar_summary_chars=9000" in out
    assert "nar_dynamic_entries=42" in out


def test_breakdown_lists_all_modules_in_emitted_order():
    """R4d: every module is listed (no cap, so the per-turn grower stays
    diffable across rounds) and the order is the EMITTED one — (priority,
    name), exactly what _build_module_instructions_prompt concatenated."""
    sizes = [100, 900, 300, 800, 50, 700, 600]
    # Priorities with a deliberate tie (M1/M2 both at 1) so the secondary
    # name key is exercised.
    priorities = [3, 1, 1, 2, 9, 0, 5]
    mods = [_mi(f"M{i}", size, prio) for i, (size, prio) in enumerate(zip(sizes, priorities))]
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="a", total_chars=1, part_sizes={}, module_instructions_list=mods, narrative_meta={},
    ))
    mods_section = out.split("modules:", 1)[1]
    for i, size in enumerate(sizes):
        assert f"M{i}={size}" in mods_section, f"M{i}={size}"
    # Emitted order: M5(p0) M1(p1) M2(p1) M3(p2) M0(p3) M6(p5) M4(p9)
    emitted = ["M5=700", "M1=900", "M2=300", "M3=800", "M0=100", "M6=600", "M4=50"]
    positions = [mods_section.index(token) for token in emitted]
    assert positions == sorted(positions), mods_section


def test_breakdown_module_order_survives_a_shuffled_input_list():
    """The printed order must be the emitted one, not the caller's list
    order — otherwise the log and the prompt could disagree."""
    mods = [_mi("Awareness", 4018, 3), _mi("SocialNetwork", 4880, 3), _mi("Chat", 500, 1)]

    def _section(instructions):
        out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
            agent_id="a", total_chars=1, part_sizes={},
            module_instructions_list=instructions, narrative_meta={},
        ))
        return out.split("modules:", 1)[1]

    assert _section(mods) == _section(list(reversed(mods)))


# =========================================================================
# R4d: prefix-bucket hashes
# =========================================================================


def test_breakdown_carries_the_three_prefix_bucket_hashes():
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="a", total_chars=40000, part_sizes={},
        module_instructions_list=[], narrative_meta={},
        ctx_sha256="abc123def456", prompt_text="P" * 40000,
    ))
    # existing fields untouched (log tooling parses these)
    assert "[SYSPROMPT-BREAKDOWN]" in out
    assert "total=40000" in out
    assert "ctx_sha256=abc123def456" in out
    for label in ("pfx2k=", "pfx8k=", "pfx32k="):
        assert label in out, label
    # 6 hex chars each
    for label in ("pfx2k", "pfx8k", "pfx32k"):
        value = out.split(f"{label}=", 1)[1].split()[0]
        assert len(value) == 6 and all(c in "0123456789abcdef" for c in value), value


def test_prefix_buckets_omitted_when_no_text_is_passed():
    """Callers that only report sizes (tests, direct callers) keep the old
    line shape exactly."""
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="a", total_chars=1, part_sizes={},
        module_instructions_list=[], narrative_meta={}, ctx_sha256="deadbeef",
    ))
    assert out.rstrip().endswith("ctx_sha256=deadbeef")
    assert "pfx2k=" not in out


def test_prefix_buckets_localize_a_same_length_divergence():
    """The whole point: a same-length substitution deep in the prompt leaves
    the earlier buckets equal and flips only the buckets that cover it."""
    base = "A" * 40000
    late = base[:10000] + "B" + base[10001:]  # same length, differs at 10000

    a = ContextRuntime._prefix_bucket_hashes(base)
    b = ContextRuntime._prefix_bucket_hashes(late)

    a_map = dict(tok.split("=") for tok in a.split())
    b_map = dict(tok.split("=") for tok in b.split())
    assert a_map["pfx2k"] == b_map["pfx2k"]  # break is beyond 2K
    assert a_map["pfx8k"] == b_map["pfx8k"]  # ...and beyond 8K
    assert a_map["pfx32k"] != b_map["pfx32k"]  # localized to 8K-32K


def test_prefix_buckets_stable_for_identical_text_and_short_input():
    assert ContextRuntime._prefix_bucket_hashes("hello") == ContextRuntime._prefix_bucket_hashes("hello")
    # A prompt shorter than a bucket still hashes (slice just returns it all),
    # so the field is never silently missing for a small prompt.
    assert "pfx32k=" in ContextRuntime._prefix_bucket_hashes("short")
    assert ContextRuntime._prefix_bucket_hashes("") == ""


def test_breakdown_handles_empty_narrative_meta():
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="a", total_chars=1, part_sizes={}, module_instructions_list=[], narrative_meta={},
    ))
    assert "narrative: n/a" in out


def test_dump_disabled_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("NARRA_SYSPROMPT_DUMP_DIR", raising=False)
    ContextRuntime._maybe_dump_system_prompt("a", "PROMPT", {}, [])
    assert list(tmp_path.iterdir()) == []  # nothing written anywhere


def test_dump_writes_file_with_header_when_env_set(tmp_path, monkeypatch):
    monkeypatch.setenv("NARRA_SYSPROMPT_DUMP_DIR", str(tmp_path))
    ContextRuntime._maybe_dump_system_prompt(
        "agent_x", "THE FULL PROMPT BODY", {"modules": 20, "narrative": 5}, [_mi("ChatModule", 20)]
    )
    files = list(tmp_path.glob("agent_x_*.txt"))
    assert len(files) == 1
    body = files[0].read_text()
    assert "# agent=agent_x total=20" in body
    assert "ChatModule=20" in body
    assert "THE FULL PROMPT BODY" in body


def test_dump_header_modules_are_in_emitted_order(tmp_path, monkeypatch):
    """R4d: the dump header shares the breakdown line's ordering authority, so
    diffing two dumps surfaces a same-length block reorder."""
    monkeypatch.setenv("NARRA_SYSPROMPT_DUMP_DIR", str(tmp_path))
    mods = [_mi("SocialNetwork", 4880, 3), _mi("Awareness", 4018, 3), _mi("Chat", 500, 1)]
    ContextRuntime._maybe_dump_system_prompt("agent_y", "BODY", {}, mods)

    header = tmp_path.glob("agent_y_*.txt").__next__().read_text().split("# modules:", 1)[1]
    emitted = ["Chat=500", "Awareness=4018", "SocialNetwork=4880"]
    positions = [header.index(token) for token in emitted]
    assert positions == sorted(positions), header
