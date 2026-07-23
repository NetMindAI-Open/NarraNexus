"""
@file_name: test_url_artifact.py
@author: Bin Liang
@date: 2026-07-22
@description: DB-backed tests for URL-tab artifacts (ArtifactService.open_url /
set_embed_mode).

The embed probe is monkeypatched to a fixed verdict so these tests exercise
doc I/O + registration + override rewrite without real network. SSRF rejection
of an internal initial URL is covered too.
"""
from __future__ import annotations

import json
import os

import pytest

from xyz_agent_context.artifact import ArtifactError, ArtifactService
from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.schema.artifact_schema import EmbedVerdict, UrlArtifactDoc
from xyz_agent_context.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)
    (base / WS_REL).mkdir(parents=True)

    # Deterministic probe: pretend the site is embeddable, no network.
    async def fake_probe(url, *, our_scheme, resolver=None, client=None):
        return EmbedVerdict(recommended="iframe", reason="no-blocking-headers", probe_status="ok")

    # Accept any public-looking URL through the SSRF gate (no real DNS).
    async def fake_assert(url, *, resolver=None):
        if "internal" in url:
            from xyz_agent_context.utils.url_safety import UnsafeUrlError
            raise UnsafeUrlError("blocked")
        return ["93.184.216.34"]

    # Deterministic page-text (no real network); "notext" URLs yield None.
    async def fake_page_text(url, *, resolver=None, client=None):
        return None if "notext" in url else f"captured text of {url}"

    import xyz_agent_context.artifact._artifact_impl.url_artifact as ua
    monkeypatch.setattr(ua, "probe_url", fake_probe)
    monkeypatch.setattr(ua, "assert_public_http_url", fake_assert)
    monkeypatch.setattr(ua, "fetch_page_text", fake_page_text)

    service = ArtifactService(db_client)
    repo = ArtifactRepository(db_client)
    yield {"db": db_client, "service": service, "repo": repo, "base": base}


@pytest.mark.asyncio
async def test_open_url_writes_doc_and_registers(env):
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://grafana.example/d/xyz", title="Grafana",
    )
    art = await env["repo"].get_by_id(result.artifact_id)
    assert art is not None
    assert art.kind == "application/x-url"
    assert art.pinned is True  # session_id=None → agent-scoped
    assert art.title == "Grafana"
    # The entry file is a UrlArtifactDoc under tabs/<slug>/.
    assert art.file_path.startswith(f"{WS_REL}/tabs/")
    assert art.file_path.endswith("/page.url.json")

    abs_path = env["base"] / art.file_path
    doc = UrlArtifactDoc.model_validate_json(abs_path.read_text())
    assert doc.url == "https://grafana.example/d/xyz"
    assert doc.embed is not None
    assert doc.embed.recommended == "iframe"
    assert doc.embed.effective_mode == "iframe"


@pytest.mark.asyncio
async def test_open_url_writes_agent_readable_content(env):
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://grafana.example/d/xyz", title="Grafana",
    )
    art = await env["repo"].get_by_id(result.artifact_id)
    import os
    content_path = env["base"] / os.path.dirname(art.file_path) / "content.md"
    assert content_path.exists()
    body = content_path.read_text()
    assert "captured text of https://grafana.example/d/xyz" in body
    assert "Grafana" in body  # title header


@pytest.mark.asyncio
async def test_open_url_content_written_even_when_extraction_fails(env):
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://notext.example/", title="NoText",
    )
    art = await env["repo"].get_by_id(result.artifact_id)
    import os
    content_path = env["base"] / os.path.dirname(art.file_path) / "content.md"
    assert content_path.exists()  # always written so the state-block hint is valid
    assert "could not be captured" in content_path.read_text()


@pytest.mark.asyncio
async def test_open_url_title_defaults_to_url(env):
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://no-title.example/", title=None,
    )
    art = await env["repo"].get_by_id(result.artifact_id)
    assert art.title == "https://no-title.example/"


@pytest.mark.asyncio
async def test_open_url_rejects_internal_target(env):
    with pytest.raises(ArtifactError):
        await env["service"].open_url(
            agent_id="agent_x", user_id="user_y", session_id=None,
            url="http://internal.service/admin", title="evil",
        )


@pytest.mark.asyncio
async def test_each_url_tab_gets_isolated_subdir(env):
    r1 = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://a.example/", title="A",
    )
    r2 = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://b.example/", title="B",
    )
    a1 = await env["repo"].get_by_id(r1.artifact_id)
    a2 = await env["repo"].get_by_id(r2.artifact_id)
    dir1 = os.path.dirname(a1.file_path)
    dir2 = os.path.dirname(a2.file_path)
    assert dir1 != dir2  # isolated roots — one tab can't read the other's json


@pytest.mark.asyncio
async def test_set_embed_mode_writes_override(env):
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://flip.example/", title="Flip",
    )
    aid = result.artifact_id

    await env["service"].set_embed_mode(agent_id="agent_x", artifact_id=aid, mode="stream")
    art = await env["repo"].get_by_id(aid)
    doc = UrlArtifactDoc.model_validate_json((env["base"] / art.file_path).read_text())
    assert doc.embed.user_override == "stream"
    assert doc.embed.effective_mode == "stream"  # override wins over iframe recommend

    # Clearing the override restores the recommendation.
    await env["service"].set_embed_mode(agent_id="agent_x", artifact_id=aid, mode=None)
    art2 = await env["repo"].get_by_id(aid)
    doc2 = UrlArtifactDoc.model_validate_json((env["base"] / art2.file_path).read_text())
    assert doc2.embed.user_override is None
    assert doc2.embed.effective_mode == "iframe"


@pytest.mark.parametrize("evil_url", [
    "https://agent.narra.nexus/some/page",   # exact
    "https://AGENT.narra.nexus/x",           # case — browser same-origin
    "https://Agent.Narra.Nexus/x",           # mixed case
    "https://agent.narra.nexus:443/x",       # explicit default port
    "https://u@agent.narra.nexus/x",         # userinfo
    "https://u:p@agent.narra.nexus:443/x",   # userinfo + default port
])
@pytest.mark.asyncio
async def test_open_url_rejects_self_origin_variants(env, monkeypatch, evil_url):
    # A URL that a BROWSER reads as our own origin must be refused, no matter
    # how it is spelled — else the allow-same-origin iframe reaches the token.
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "public_base_url", "https://agent.narra.nexus", raising=False)
    with pytest.raises(ArtifactError):
        await env["service"].open_url(
            agent_id="agent_x", user_id="user_y", session_id=None,
            url=evil_url, title="self",
        )


@pytest.mark.asyncio
async def test_open_url_self_origin_guard_uses_request_origin_when_config_unset(env, monkeypatch):
    # Even with public_base_url unset, the app_origin the HTTP route derives
    # from the request closes the guard (defense in depth).
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "public_base_url", "", raising=False)
    with pytest.raises(ArtifactError):
        await env["service"].open_url(
            agent_id="agent_x", user_id="user_y", session_id=None,
            url="https://dev-agent.narra.nexus:443/x", title="self",
            app_origin="https://dev-agent.narra.nexus",
        )


@pytest.mark.asyncio
async def test_open_url_allows_genuine_third_party(env, monkeypatch):
    # A different host is NOT self-origin — must be allowed.
    from xyz_agent_context.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "public_base_url", "https://agent.narra.nexus", raising=False)
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://notagent.narra.nexus/x", title="third-party",
        app_origin="https://agent.narra.nexus",
    )
    assert result.artifact_id.startswith("art_")


@pytest.mark.asyncio
async def test_set_embed_mode_missing_doc_raises_content_gone(env):
    from xyz_agent_context.artifact import ArtifactContentGone
    result = await env["service"].open_url(
        agent_id="agent_x", user_id="user_y", session_id=None,
        url="https://gone.example/", title="Gone",
    )
    art = await env["repo"].get_by_id(result.artifact_id)
    # Simulate the agent deleting the workspace file (a real pointer-model state).
    (env["base"] / art.file_path).unlink()
    with pytest.raises(ArtifactContentGone):
        await env["service"].set_embed_mode(
            agent_id="agent_x", artifact_id=result.artifact_id, mode="stream",
        )


@pytest.mark.asyncio
async def test_set_embed_mode_rejects_non_url_artifact(env):
    from xyz_agent_context.artifact import ArtifactNotFound
    # Register a normal html artifact, then try to flip its embed mode.
    (env["base"] / WS_REL / "r").mkdir()
    (env["base"] / WS_REL / "r" / "i.html").write_text("<p>x</p>")
    html = await env["service"].register(
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path="r/i.html", title="html",
    )
    with pytest.raises(ArtifactNotFound):
        await env["service"].set_embed_mode(
            agent_id="agent_x", artifact_id=html.artifact_id, mode="stream",
        )
