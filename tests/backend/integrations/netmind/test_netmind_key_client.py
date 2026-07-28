"""
@file_name: test_netmind_key_client.py
@author: NarraNexus
@date: 2026-07-02
@description: Unit tests for NetmindKeyClient (NetMind key-management proxy).

Mocks HTTP with httpx.MockTransport. Covers create-then-list with a unique
per-call name, the HTTP-200 + {success:false} envelope, delete, and the
two-value error contract.
"""
from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from backend.integrations.netmind.netmind_key_client import (
    KeyAuthError,
    KeyUpstreamError,
    NetmindKeyClient,
)


def _client_with(handler) -> NetmindKeyClient:
    return NetmindKeyClient(
        base_url="https://key.test.invalid", transport=httpx.MockTransport(handler)
    )


def _form(request: httpx.Request) -> dict:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


@pytest.mark.asyncio
async def test_create_key_uses_unique_name_and_returns_token_and_id():
    seen = {"add_name": None, "query_name": None}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("token") == "Bearer jwt"  # not loginToken
        form = _form(request)
        if request.url.path.endswith("/addApiToken"):
            seen["add_name"] = form.get("name")
            return httpx.Response(200, json={"success": True})
        # queryApitokenList — echo a row matching the queried (unique) name.
        seen["query_name"] = form.get("name")
        return httpx.Response(200, json={
            "success": True,
            "data": [
                {"id": 77, "name": form.get("name"), "apitoken": "key_new", "createTime": 200},
                # a same-prefix but DIFFERENT-name stale key must be ignored:
                {"id": 1, "name": "NarraNexus-old", "apitoken": "key_old", "createTime": 999},
            ],
        })

    minted = await _client_with(handler).create_key("jwt")
    assert minted.apitoken == "key_new"
    assert minted.token_id == 77
    # unique per-call name, and add/query used the SAME name
    assert seen["add_name"] and seen["add_name"].startswith("NarraNexus-")
    assert seen["add_name"] == seen["query_name"]


@pytest.mark.asyncio
async def test_create_key_no_exact_name_match_is_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/addApiToken"):
            return httpx.Response(200, json={"success": True})
        # list returns only unrelated keys — must NOT pick one of them
        return httpx.Response(200, json={
            "success": True,
            "data": [{"id": 1, "name": "SomethingElse", "apitoken": "not_ours", "createTime": 1}],
        })

    with pytest.raises(KeyUpstreamError):
        await _client_with(handler).create_key("jwt")


@pytest.mark.asyncio
async def test_map_non_dict_does_not_crash():
    def handler(request: httpx.Request) -> httpx.Response:
        form = _form(request)
        if request.url.path.endswith("/addApiToken"):
            return httpx.Response(200, json={"success": True})
        # apitoken absent, map is a string (malformed) — must raise KeyUpstreamError,
        # NOT AttributeError/500.
        return httpx.Response(200, json={
            "success": True,
            "data": [{"id": 5, "name": form.get("name"), "map": "oops", "createTime": 1}],
        })

    with pytest.raises(KeyUpstreamError):
        await _client_with(handler).create_key("jwt")


@pytest.mark.asyncio
async def test_not_loggedin_maps_to_auth_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "errorcode": "NOT_LOGGEDIN"})

    with pytest.raises(KeyAuthError):
        await _client_with(handler).create_key("bad")


@pytest.mark.asyncio
async def test_non_auth_failure_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"success": False, "errorcode": "INTERNAL_ERROR"})

    with pytest.raises(KeyUpstreamError):
        await _client_with(handler).create_key("jwt")


@pytest.mark.asyncio
async def test_empty_token_list_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/addApiToken"):
            return httpx.Response(200, json={"success": True})
        return httpx.Response(200, json={"success": True, "data": []})

    with pytest.raises(KeyUpstreamError):
        await _client_with(handler).create_key("jwt")


@pytest.mark.asyncio
async def test_network_error_maps_to_upstream_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom")

    with pytest.raises(KeyUpstreamError):
        await _client_with(handler).create_key("jwt")


@pytest.mark.asyncio
async def test_delete_key_is_best_effort_and_never_raises():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    # must swallow the error (cleanup must never mask the original failure)
    await _client_with(handler).delete_key("jwt", 77)


@pytest.mark.asyncio
async def test_delete_key_noop_when_id_none():
    called = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        called["n"] += 1
        return httpx.Response(200, json={"success": True})

    await _client_with(handler).delete_key("jwt", None)
    assert called["n"] == 0  # no request made
