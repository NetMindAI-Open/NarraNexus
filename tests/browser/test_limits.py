"""
@file_name: test_limits.py
@date: 2026-10-08
@description: Model-supplied browser arguments are bounded before they reach CDP.

At the limit is accepted; one character over is refused with an actionable
message, and nothing is sent to the page.
"""
from __future__ import annotations

import pytest

from narranexus.platform.browser._browser_impl.actions import action_expression
from narranexus.platform.browser._browser_impl.limits import (
    MAX_FIELD_TEXT_CHARS,
    MAX_SCRIPT_CHARS,
    MAX_SELECTOR_CHARS,
)
from narranexus.platform.browser._browser_impl.policy import BrowserPolicy, OriginPolicy
from narranexus.platform.browser._browser_impl.read import snapshot_expression
from narranexus.platform.browser._browser_impl.visual import view_expression
from tests.browser.test_session import make_session


def test_fill_text_and_select_value_are_bounded():
    action_expression("fill", selector="#q", text="x" * MAX_FIELD_TEXT_CHARS)
    action_expression("select", selector="#s", value="v" * MAX_FIELD_TEXT_CHARS)
    with pytest.raises(ValueError, match="text is longer than"):
        action_expression("fill", selector="#q", text="x" * (MAX_FIELD_TEXT_CHARS + 1))
    with pytest.raises(ValueError, match="value is longer than"):
        action_expression("select", selector="#s", value="v" * (MAX_FIELD_TEXT_CHARS + 1))


@pytest.mark.parametrize("build", [
    lambda selector: action_expression("click", selector=selector),
    lambda selector: snapshot_expression(selector=selector),
    lambda selector: view_expression(selector=selector),
], ids=["act", "read", "look"])
def test_every_tool_bounds_selectors_the_same_way(build):
    build("#" + "a" * (MAX_SELECTOR_CHARS - 1))
    with pytest.raises(ValueError, match="selector is longer than"):
        build("#" + "a" * MAX_SELECTOR_CHARS)


@pytest.mark.asyncio
async def test_an_oversized_script_is_refused_before_the_page_sees_it():
    # Scripts are allowed here, so only the size bound can refuse it.
    session, cdp, _ = make_session(BrowserPolicy(default_origin_policy=OriginPolicy(full_cdp_access="allow")))
    cdp.calls.clear()
    result = await session.run_script("1;" * (MAX_SCRIPT_CHARS // 2 + 1))
    assert result["outcome"] == "ERROR"
    assert "longer than" in result["message"]
    assert not any(method == "Runtime.evaluate" and "1;1;" in str(params) for method, params in cdp.calls)

