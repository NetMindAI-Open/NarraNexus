"""
@file_name: test_module_instructions_format_safe.py
@author:
@date: 2026-09-22
@description: A literal brace in a module prompt must be escaped.

``XYZBaseModule.contribute_instructions`` does::

    instruction = self.instructions.format(**local_ctx_data)

so every ``{...}`` in a module's prompt is read as a replacement field. A
module may legitimately use one — ``{awareness}`` is filled from a key the
module injects via its hook — but a brace that is part of *example code* is
not a placeholder, and `.format()` raises ``KeyError`` **inside context
assembly**, failing the whole turn.

That is not theoretical. A JavaScript example added to BrowserModule's prompt
on 2026-09-22 contained ``{ document.querySelector(...) }`` and every turn for
any agent with the module enabled died with ``KeyError: ' document'`` at
``step_3_assemble_context`` — an error that names a pipeline phase, not the
file that caused it.

The rule asserted here is the one that separates the two cases without having
to know each module's runtime keys: **a replacement field must look like a
name**. ``{awareness}`` does; ``{ document.querySelector('#kw') }`` does not.
"""
from __future__ import annotations

import string

import pytest

from narranexus.platform.module_system import module_registry

#: A field may be a name, optionally with attribute or index access, and may
#: carry a conversion/format spec. Anything with spaces, quotes, parentheses
#: or operators is code that should have been written as {{ }}.
def _looks_like_a_field(name: str) -> bool:
    head = name.split(".")[0].split("[")[0]
    return head.isidentifier() or head.isdigit() or head == ""


def _bad_fields(template: str) -> list[str]:
    return [
        name
        for _lit, name, _spec, _conv in string.Formatter().parse(template)
        if name is not None and not _looks_like_a_field(name)
    ]


@pytest.mark.parametrize("module_name", sorted(module_registry))
def test_module_instructions_have_no_unescaped_literal_braces(module_name):
    module_class = module_registry[module_name]
    try:
        module = module_class("agent_probe")
    except Exception:
        pytest.skip(f"{module_name} needs more than an agent_id to construct")

    template = getattr(module, "instructions", "") or ""
    if not template:
        return

    bad = _bad_fields(template)
    assert not bad, (
        f"{module_name}'s instructions contain {len(bad)} brace group(s) that are not "
        f"placeholders, e.g. {bad[0]!r}. `contribute_instructions` runs "
        f"`instructions.format(**ctx_data)`, so these raise KeyError during context "
        f"assembly and kill the turn. Write literal braces as {{{{ and }}}}."
    )


def test_the_rule_catches_the_case_that_caused_the_outage():
    """The exact shape that broke turns on 2026-09-22."""
    js = "run this: (() => { document.querySelector('#kw').value = 'x'; })()"
    assert _bad_fields(js), "the scan must reject an unescaped JS block"


def test_the_rule_allows_a_real_placeholder():
    assert _bad_fields("Profile:\n{awareness}\n") == []


def test_the_rule_allows_escaped_braces():
    assert _bad_fields("write it as {{ like this }}") == []
