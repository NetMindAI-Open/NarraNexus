"""
@file_name: test_helper_strict_schema.py
@date: 2026-09-09
@description: Every helper structured-output model must reach the provider as
an OpenAI-strict-compatible JSON schema.

Dev logs 2026-08-25: 104 x HTTP 400 in one day once the helper slot pointed at
gpt-5.4-mini — ``response_format = json_schema`` with ``strict: true`` was sent
the raw ``model_json_schema()`` of ``ContinuityOutput`` / ``UnifiedMatchOutput``,
which lacks ``additionalProperties: false`` and lists defaulted fields outside
``required``; OpenAI's strict validator rejects both. The ladder then marked
``json_schema`` unsupported and fell to ``json_object`` — the schema level the
ladder exists to prefer was never reachable on a strict provider.

The fix is one seam: ``build_strict_json_schema`` (the same rewrite the
OpenAI SDK applies for ``client.beta.chat.completions.parse``) is what the
json_schema rung sends. These tests pin (a) that rewrite for EVERY model the
codebase passes as ``output_type`` (discovered by scanning the source tree,
so a new helper model is covered without editing this file) and (b) that the
client really sends ``strict: true`` with the rewritten schema — the contract
is only real if both halves hold — and (c) that a schema the strict rewrite
REJECTS (an open object: a ``dict[str, ...]`` field) does not break the call:
the rung is skipped for that output type and the ladder degrades to
``json_object`` exactly as a provider 400 would make it, with the other
output types on the same model untouched.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from agents.exceptions import UserError
from pydantic import BaseModel, Field

from narranexus.platform.agent_framework.adapters import openai_agents as mod
from narranexus.platform.agent_framework.adapters.openai_agents import (
    OpenAIAgentsSDK,
    build_strict_json_schema,
)
from narranexus.platform.narrative._narrative_impl.continuity import ContinuityOutput

_REPO = Path(__file__).resolve().parents[2]
_OUTPUT_TYPE_RE = re.compile(r"output_type=([A-Za-z_][A-Za-z0-9_]*)")


def _module_name(path: Path) -> str | None:
    rel = path.relative_to(_REPO)
    parts = rel.with_suffix("").parts
    if parts[0] == "src":
        return ".".join(parts[1:])
    if parts[0] == "plugins" and len(parts) > 3 and parts[2] == "src":
        return ".".join(parts[3:])
    return None


def _discover_output_models() -> list[tuple[str, type[BaseModel]]]:
    """Every ``output_type=<Name>`` call site under src/ and plugins/*/src/,
    resolved to the Pydantic class in the SAME module (helper models are
    defined next to their one caller)."""
    found: dict[str, type[BaseModel]] = {}
    roots = [_REPO / "src", *sorted((_REPO / "plugins").glob("*/src"))]
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            names = {
                n for n in _OUTPUT_TYPE_RE.findall(text)
                if n not in {"output_type", "None"}
            }
            if not names:
                continue
            module_name = _module_name(path)
            if module_name is None:
                continue
            module = importlib.import_module(module_name)
            for name in sorted(names):
                cls = getattr(module, name, None)
                if isinstance(cls, type) and issubclass(cls, BaseModel):
                    found[f"{module_name}.{name}"] = cls
    return sorted(found.items())


_DISCOVERED = _discover_output_models()


def _object_nodes(node, path=""):
    """Yield (path, node) for every object-shaped node, including $defs."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield path, node
        for key, value in node.items():
            yield from _object_nodes(value, f"{path}/{key}")
    elif isinstance(node, list):
        for i, value in enumerate(node):
            yield from _object_nodes(value, f"{path}[{i}]")


def _strict_violations(schema: dict) -> list[str]:
    out = []
    for path, node in _object_nodes(schema):
        if node.get("additionalProperties") is not False:
            out.append(f"{path or '/'}: additionalProperties must be false")
        props = sorted((node.get("properties") or {}).keys())
        if sorted(node.get("required") or []) != props:
            out.append(f"{path or '/'}: required must list every property")
    return out


# ── (a) every discovered helper model ──────────────────────────────────


def test_discovery_finds_the_helper_models():
    names = {qualified.rsplit(".", 1)[1] for qualified, _ in _DISCOVERED}
    # The two models from the 2026-08-25 incident must be in the sweep.
    assert {"ContinuityOutput", "UnifiedMatchOutput"} <= names
    assert len(_DISCOVERED) >= 16, sorted(names)


@pytest.mark.parametrize(
    "qualified, model", _DISCOVERED, ids=[q for q, _ in _DISCOVERED]
)
def test_every_helper_output_model_builds_a_strict_schema(qualified, model):
    schema = build_strict_json_schema(model)
    assert _strict_violations(schema) == []
    # Field set is preserved — the rewrite tightens, never drops.
    assert set(schema["properties"]) == set(model.model_fields)


def test_raw_pydantic_schema_is_not_strict_for_the_incident_model():
    """Documents why the seam exists: the raw schema is what used to be sent."""
    raw = ContinuityOutput.model_json_schema()
    violations = _strict_violations(raw)
    assert any("additionalProperties" in v for v in violations)
    assert any("required" in v for v in violations)  # confidence/reason default


class _AlreadyStrict(BaseModel):
    model_config = {"extra": "forbid"}
    flag: bool = Field(description="required, no default")


def test_already_strict_model_passes_through_unchanged():
    """Positive / allowed case: a compliant schema is left as is."""
    raw = _AlreadyStrict.model_json_schema()
    assert _strict_violations(raw) == []
    assert build_strict_json_schema(_AlreadyStrict) == raw


# ── (b) the client sends strict=true with the rewritten schema ────────────


def _fake_response(content: str):
    msg = MagicMock()
    msg.content = content
    choice = MagicMock()
    choice.message = msg
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = MagicMock(prompt_tokens=0, completion_tokens=0)
    return resp


@pytest.fixture(autouse=True)
def _clean_capability_cache():
    mod._response_format_capability.clear()
    mod._structured_output_blocklist.clear()
    mod._strict_rewrite_unsupported.clear()
    yield
    mod._response_format_capability.clear()
    mod._structured_output_blocklist.clear()
    mod._strict_rewrite_unsupported.clear()


@pytest.mark.asyncio
async def test_json_schema_rung_sends_strict_true_with_the_strict_schema():
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response(
            '{"is_continuous": true, "confidence": 0.9, "reason": "same topic"}'
        )
    )
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini",
        instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    assert result.final_output.is_continuous is True

    (call,) = client.chat.completions.create.call_args_list
    rf = call.kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "ContinuityOutput"
    sent = rf["json_schema"]["schema"]
    assert _strict_violations(sent) == [], _strict_violations(sent)
    assert sent == build_strict_json_schema(ContinuityOutput)


def _prompt_schema(client) -> dict:
    system = client.chat.completions.create.call_args.kwargs["messages"][0]
    assert system["role"] == "system"
    return json.loads(system["content"].split("Schema: ", 1)[1])


@pytest.mark.asyncio
async def test_prompt_hint_keeps_the_raw_pydantic_schema():
    """The strict rewrite is for the provider-enforced rung ONLY. The schema
    quoted in the system prompt — what the json_object / prompt-only rungs
    and every non-OpenAI provider read — stays the raw Pydantic schema, so a
    defaulted field (confidence / reason) is still optional there and a weak
    model is not pushed to invent a value for it."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response('{"is_continuous": true, "reason": "same"}')
    )
    sdk = OpenAIAgentsSDK()
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    hinted = _prompt_schema(client)
    assert hinted == ContinuityOutput.model_json_schema()
    assert hinted != build_strict_json_schema(ContinuityOutput)
    assert "confidence" not in hinted.get("required", [])
    # …while the very same call sent the strict rewrite on the rung.
    rf = client.chat.completions.create.call_args.kwargs["response_format"]
    assert rf["json_schema"]["schema"] == build_strict_json_schema(ContinuityOutput)


@pytest.mark.asyncio
async def test_parse_stays_lenient_to_extra_keys_on_lower_rungs():
    """Strictness is enforced provider-side only. The json_object / prompt-only
    rungs have no schema enforcement, and a weaker model that adds a stray key
    must still parse — the rewrite must not leak into client-side validation."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response(
            '{"is_continuous": false, "confidence": 0.2, "reason": "new", "extra": 1}'
        )
    )
    mod._response_format_capability[mod._capability_key("m")] = {"json_object"}
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        client, "m", instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    assert result.final_output.is_continuous is False
    rf = client.chat.completions.create.call_args.kwargs["response_format"]
    assert rf == {"type": "json_object"}


# ── (c) a schema the strict rewrite rejects degrades, never raises ────────


class _OpenObjectOutput(BaseModel):
    """Not in the repo's helper set on purpose (a third-party / agent-written
    plugin shape): a ``dict`` field is an open object, which OpenAI strict
    mode cannot express."""
    verdict: bool
    attributes: dict[str, str] = Field(default_factory=dict)


def test_open_object_schema_is_rejected_by_the_strict_rewrite():
    """Documents the failure mode the ladder must absorb: the rewrite is a
    LOCAL exception, not a provider 400."""
    with pytest.raises(UserError):
        build_strict_json_schema(_OpenObjectOutput)


@pytest.mark.asyncio
async def test_rejected_strict_schema_degrades_to_json_object(monkeypatch):
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response('{"verdict": true, "attributes": {"k": "v"}}')
    )
    audits: list[tuple[str, dict]] = []

    async def _audit(event_type, detail):
        audits.append((event_type, detail))

    monkeypatch.setattr(mod, "_audit_framework_downgrade", _audit)
    sdk = OpenAIAgentsSDK()
    result = await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=_OpenObjectOutput, max_tokens=200,
    )
    assert result.final_output.verdict is True
    assert result.final_output.attributes == {"k": "v"}

    # Exactly one request went out, on the json_object rung: the strict rung
    # was skipped locally, not sent and rejected.
    (call,) = client.chat.completions.create.call_args_list
    assert call.kwargs["response_format"] == {"type": "json_object"}
    assert mod._last_llm_call_info.get()["response_format"] == "json_object"
    # Audited like any other self-downgrade.
    assert [a[0] for a in audits] == ["strict_schema_rewrite_rejected"]
    assert audits[0][1]["output_type"] == "_OpenObjectOutput"
    assert audits[0][1]["level"] == "json_schema"
    # Cached on the output TYPE: a second call does not rebuild / re-audit.
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=_OpenObjectOutput, max_tokens=200,
    )
    assert len(audits) == 1
    assert client.chat.completions.create.call_args.kwargs["response_format"] == {
        "type": "json_object"
    }


@pytest.mark.asyncio
async def test_rejected_output_type_does_not_demote_the_model_for_others():
    """The skip is per output type. After an open-object model degraded on
    gpt-5.4-mini, a strict-compatible model on the SAME provider/model still
    gets the json_schema rung — the capability cache must not be touched."""
    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response('{"verdict": false, "attributes": {}}')
    )
    sdk = OpenAIAgentsSDK()
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=_OpenObjectOutput, max_tokens=200,
    )
    assert "json_schema" in mod._allowed_levels(mod._capability_key("gpt-5.4-mini"))

    client.chat.completions.create = AsyncMock(
        return_value=_fake_response(
            '{"is_continuous": true, "confidence": 0.9, "reason": "same topic"}'
        )
    )
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=ContinuityOutput, max_tokens=200,
    )
    rf = client.chat.completions.create.call_args.kwargs["response_format"]
    assert rf["type"] == "json_schema"
    assert rf["json_schema"]["strict"] is True


@pytest.mark.asyncio
async def test_a_same_named_model_with_a_different_shape_keeps_the_strict_rung():
    """Plugins can build output models dynamically; two `create_model("Foo")`
    in one module share a qualified name. The rejection of the open-object
    one must not switch the strict rung off for the strict-compatible one."""
    from pydantic import create_model

    open_foo = create_model("Foo", verdict=(bool, ...), attributes=(dict[str, str], {}))
    strict_foo = create_model("Foo", verdict=(bool, ...))
    assert open_foo.__qualname__ == strict_foo.__qualname__
    assert open_foo.__module__ == strict_foo.__module__

    client = MagicMock()
    client.chat.completions.create = AsyncMock(
        return_value=_fake_response('{"verdict": true, "attributes": {}}')
    )
    sdk = OpenAIAgentsSDK()
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=open_foo, max_tokens=200,
    )
    assert client.chat.completions.create.call_args.kwargs["response_format"] == {
        "type": "json_object"
    }

    client.chat.completions.create = AsyncMock(return_value=_fake_response('{"verdict": true}'))
    await sdk._fallback_chat_completion(
        client, "gpt-5.4-mini", instructions="judge", user_input="q",
        output_type=strict_foo, max_tokens=200,
    )
    assert client.chat.completions.create.call_args.kwargs["response_format"]["type"] == "json_schema"

    # The same shape still maps to one key (the skip stays cached).
    assert mod._strict_rewrite_key(open_foo) == mod._strict_rewrite_key(
        create_model("Foo", verdict=(bool, ...), attributes=(dict[str, str], {}))
    )
