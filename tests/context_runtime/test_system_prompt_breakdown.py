"""
@file_name: test_system_prompt_breakdown.py
@author: Hongyi Gu
@date: 2026-07-14
@description: Unit tests for ContextRuntime._log_system_prompt_breakdown — the
[SYSPROMPT-BREAKDOWN] diagnostic added for the system-prompt-growth incident
(2026-07). Verifies the one-line breakdown reports every Part's byte size, the
five largest module-instruction contributors (sorted desc), and the Narrative's
growth-prone sub-fields (current_summary chars, dynamic_summary entry count).
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


def test_breakdown_lists_all_modules_sorted_desc():
    mods = [_mi(f"M{i}", size) for i, size in enumerate([100, 900, 300, 800, 50, 700, 600])]
    out = _capture(lambda: ContextRuntime._log_system_prompt_breakdown(
        agent_id="a", total_chars=1, part_sizes={}, module_instructions_list=mods, narrative_meta={},
    ))
    mods_section = out.split("modules:", 1)[1]
    # ALL modules listed (no cap) so the per-turn grower is diffable across rounds
    for i, size in enumerate([100, 900, 300, 800, 50, 700, 600]):
        assert f"M{i}={size}" in mods_section, f"M{i}={size}"
    # descending by instruction length
    assert mods_section.index("M1=900") < mods_section.index("M3=800") < mods_section.index("M5=700")
    assert mods_section.index("M5=700") < mods_section.index("M6=600") < mods_section.index("M2=300")


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
