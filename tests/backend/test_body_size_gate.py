"""
@file_name: test_body_size_gate.py
@date: 2026-08-20
@description: Drift pins for the body-size middleware (review #334 r4 I2 +
r5 I1/I2): the gate's existence depends on strings in two files staying
aligned, and neither a route move nor an unmounted middleware would fail
any other test.

- every BODY_CAPS pattern must match a route REGISTERED on the real app
  (paths sampled from the live route table, not hand-copied strings);
- every REGISTERED write route (POST/PUT/PATCH) must either be covered by
  a BODY_CAPS entry or sit in the explicit exemption list below — adding a
  write endpoint without deciding its size story is a CI failure, not a
  silent gap (the r3 lesson: a Pydantic body field means FastAPI buffers
  the whole payload BEFORE any dependency runs, so an uncapped body-field
  route has no memory bound at all);
- the middleware must actually be mounted on the app;
- the mount order must keep CORS outermost (a 401/413 without an ACAO
  header is invisible to a cross-origin caller), body_size inside
  access_log (its 413s belong in the access log) and outside auth.
"""
from __future__ import annotations

import re

import pytest

from backend.main import app
from backend.middleware.body_size import BODY_CAPS, body_size_middleware

# Registered ONLY when ENABLE_MANYFOLD_API is set (backend/main.py env
# gate) — CI runs without the flag, so app.routes lacks them and neither
# the live-route assertion nor the stale check may demand them (review
# #334 r6 I1: the reverse pin covered only the env-happened-to-be-off
# half). Split by size story:
#   - _ENV_GATED_EXEMPT: no middleware cap needed, reasons per line;
#   - _ENV_GATED_CAPPED: carries a real BODY_CAPS entry (the pattern is
#     registered unconditionally; with the flag off the path 404s anyway).
_ENV_GATED_EXEMPT = frozenset({
    "/manyfold/agents",                         # small fixed-shape JSON
    "/manyfold/agents/{agent_id}",              # small fixed-shape JSON
    "/manyfold/agents/{agent_id}/files/write",  # own streamed _MAX_WRITE_BYTES gate
})
_ENV_GATED_CAPPED = frozenset({
    "/v1/chat/completions",  # MAX_CHAT_COMPLETIONS_BYTES — body field + unbounded messages
    "/api/browser/policy/{agent_id}",  # builtin.browser may be disabled
    "/api/browser/policy/{agent_id}/revoke",
    "/api/browser/runtime/source",
    "/api/browser/runtime/mode",
})

# Write routes with NO BODY_CAPS entry, by ROUTE TEMPLATE (r.path verbatim —
# sampled paths would silently detach when a path-parameter name changes).
# Every entry is an explicit "no cap needed" decision; the groups state why.
# A new write endpoint must either get a BODY_CAPS line or be added here —
# either way the size story is decided in review, not defaulted.
_NO_BODY_CAP_EXEMPT = frozenset({
    # ── uploads with their OWN in-handler limits (declared + enforced at
    #    the route; adding a middleware cap would be a second spelling of
    #    the same number) ─────────────────────────────────────────────────
    "/api/agents/{agent_id}/attachments",      # settings-driven max_bytes check
    "/api/agents/{agent_id}/files",            # same upload plumbing as attachments
    "/api/teams/{team_id}/chat/attachments",   # same upload plumbing, team-scoped
    "/api/bundle/skills/archives/upload",      # MAX_BUNDLE_BYTES declared+streamed
    "/api/bundle/import/confirm",              # MAX_BUNDLE_BYTES declared+streamed
    "/api/bundle/import/preflight",            # MAX_BUNDLE_BYTES declared+streamed
    # ── small fixed-shape JSON bodies (config / action / auth payloads of
    #    scalar fields; no user-file-scale content travels through them).
    #    A platform-wide default cap for this class is a separate decision,
    #    tracked in body_size.py.md — exempting them here documents today's
    #    state, it does not bless it forever. ─────────────────────────────
    "/api/admin/gateway-key-misuse",
    "/api/admin/migrate-identity",
    "/api/admin/quota/init",
    "/api/admin/quota/topup",
    "/api/admin/reinstate",
    "/api/admin/suspend",
    "/api/admin/warn-user",
    "/api/agent-inbox/rooms/{room_id}/read",
    "/api/agent-inbox/{message_id}/read",
    "/api/agents/{agent_id}/artifacts/register",
    "/api/agents/{agent_id}/artifacts/url",
    "/api/agents/{agent_id}/artifacts/{artifact_id}",
    "/api/agents/{agent_id}/artifacts/{artifact_id}/embed-mode",
    "/api/agents/{agent_id}/artifacts/{artifact_id}/heal",
    "/api/agents/{agent_id}/artifacts/{artifact_id}/office-edit-commit",
    "/api/agents/{agent_id}/awareness",
    "/api/agents/{agent_id}/bus-failures/{message_id}/retry",
    "/api/agents/{agent_id}/channels/{channel}/credential",
    "/api/agents/{agent_id}/chat-history/by-instance",
    "/api/agents/{agent_id}/circuit-breaker/reset",
    "/api/agents/{agent_id}/jobs",
    "/api/agents/{agent_id}/jobs/search-keywords",
    "/api/agents/{agent_id}/jobs/search-semantic",
    "/api/agents/{agent_id}/jobs/{job_id}/cancel",
    "/api/agents/{agent_id}/jobs/{job_id}/pause",
    "/api/agents/{agent_id}/jobs/{job_id}/update",
    "/api/agents/{agent_id}/llm-config/{slot_name}",
    "/api/agents/{agent_id}/mcps",
    "/api/agents/{agent_id}/mcps/validate-all",
    "/api/agents/{agent_id}/mcps/{mcp_id}",
    "/api/agents/{agent_id}/mcps/{mcp_id}/validate",
    "/api/agents/{agent_id}/memory/retain",
    "/api/agents/{agent_id}/narratives",
    "/api/agents/{agent_id}/narratives/{narrative_id}/switch",
    "/api/agents/{agent_id}/profile/update",
    "/api/agents/{agent_id}/social-network/contact",
    "/api/agents/{agent_id}/social-network/create-agent",
    "/api/agents/{agent_id}/social-network/delete-entity",
    "/api/agents/{agent_id}/social-network/extract",
    "/api/agents/{agent_id}/social-network/merge",
    "/api/agents/{agent_id}/social-network/recall",
    "/api/agents/{agent_id}/social-network/stats",
    "/api/analytics/events",
    "/api/arena/provision",
    "/api/auth/agents",
    "/api/auth/agents/{agent_id}",
    "/api/auth/create-user",
    "/api/auth/funnel-report",
    "/api/auth/login",
    "/api/auth/netmind-login",
    "/api/auth/onboarding",
    "/api/auth/settings/analytics",
    "/api/auth/settings/reply-language",
    "/api/auth/settings/telemetry",
    "/api/auth/signup",
    "/api/auth/signup/send-code",
    "/api/auth/timezone",
    "/api/billing/cancel",
    "/api/billing/reactivate",
    "/api/billing/recharge",
    # Browser runtime install is a pure action: both take NO body at all, so a
    # size cap would be a limit on nothing. The bytes they move (a ~150 MB
    # Chromium) travel out-of-band from the vendor CDN, never through us.
    "/api/browser/runtime/install",
    "/api/browser/runtime/install/cancel",
    "/api/billing/subscribe",
    "/api/bundle/export",
    "/api/bundle/export/preview/artifacts",
    "/api/bundle/export/preview/bus-channels",
    "/api/bundle/export/preview/mcps",
    "/api/bundle/import/from-url",
    "/api/dashboard/jobs/{job_id}/pause",
    "/api/dashboard/jobs/{job_id}/resume",
    "/api/dashboard/jobs/{job_id}/retry",
    "/api/dashboard/jobs/{job_id}/schedule",
    "/api/feedback",
    "/api/home-assistant/binding",
    "/api/home-assistant/test",
    "/api/home-assistant/verify",
    "/api/jobs/complex",
    "/api/jobs/{job_id}",
    "/api/jobs/{job_id}/cancel",
    "/api/jobs/{job_id}/pause",
    "/api/lark/auth/complete",
    "/api/lark/auth/login",
    "/api/marketplace/skills/publish",
    "/api/marketplace/skills/{skill_id}/install",
    "/api/marketplace/teams/templates",
    "/api/marketplace/teams/templates/{template_id}/install-preflight",
    "/api/migrate/apply",
    "/api/migrate/hurry",
    "/api/migrate/scan",
    "/api/narramessenger/prewarm",
    "/api/notices/{message_id}/read",
    "/api/notifications/read-all",
    "/api/notifications/{notification_id}/read",
    # plugin install/uninstall take NO request body — the plugin id is a path
    # param and everything else is server-side (registry-pinned versions). A
    # body cap would guard bytes that never arrive.
    "/api/plugins/{plugin_id}/install",
    "/api/plugins/{plugin_id}/uninstall",
    "/api/providers",
    "/api/providers/agent-framework",
    "/api/providers/onboard",
    "/api/providers/slots/{slot_name}",
    "/api/providers/sync-defaults",
    "/api/providers/test-config",
    "/api/providers/use-subscription",
    "/api/providers/{provider_id}/models",
    "/api/providers/{provider_id}/test",
    "/api/runs/{run_id}/cancel",
    "/api/skills/install",
    "/api/skills/{skill_name}/disable",
    "/api/skills/{skill_name}/enable",
    "/api/skills/{skill_name}/env",
    "/api/skills/{skill_name}/study",
    "/api/teams",
    "/api/teams/{team_id}",
    "/api/teams/{team_id}/artifacts/{artifact_id}/view-token",
    "/api/teams/{team_id}/bulletin",
    "/api/teams/{team_id}/bulletin/{entry_id}",
    "/api/teams/{team_id}/chat/messages",
    "/api/teams/{team_id}/members",
    "/api/teams/{team_id}/patrol",
    "/api/teams/{team_id}/work-items/{item_id}/resume",
    "/api/wechat/qrcode/poll",
    "/api/wechat/qrcode/start",
})


def _sampled(path: str) -> str:
    # Wildcard every placeholder instead of maintaining a hand-copied table
    # (r6 M4): a new {param_name} costs nothing, and a future stricter cap
    # regex can't silently miss an unsampled placeholder. NOTE: {path:path}
    # collapses to a single segment — fine for the current prefix cap; a cap
    # asserting multi-segment content there would need a real sample.
    return re.sub(r"\{[^}]+\}", "x", path)


def _sampled_routes():
    out = []
    for r in app.routes:
        path = getattr(r, "path", "")
        out.append((getattr(r, "methods", set()) or set(), _sampled(path)))
    return out


def test_every_body_cap_matches_a_registered_route():
    routes = _sampled_routes()
    # env-gated routes are absent from app.routes when the flag is off —
    # their caps are anchored to the declared template list instead.
    env_gated_samples = {_sampled(t) for t in _ENV_GATED_CAPPED}
    for methods, pattern, _cap in BODY_CAPS:
        assert any(
            (m & methods) and pattern.match(p) for m, p in routes
        ) or any(pattern.match(s) for s in env_gated_samples), (
            f"BODY_CAPS pattern matches no registered route: {pattern.pattern}"
        )


def test_every_env_gated_capped_template_has_a_cap():
    """The other half of the anchor above: each template in
    _ENV_GATED_CAPPED must actually be covered by a BODY_CAPS entry —
    otherwise deleting the cap line would leave the template silently
    vouching for a gate that no longer exists. Static vs static, so this
    holds regardless of the env flag."""
    for template in _ENV_GATED_CAPPED:
        sampled = _sampled(template)
        assert any(
            pat.match(sampled) for _m, pat, _c in BODY_CAPS
        ), f"env-gated template has no BODY_CAPS entry: {template}"


def test_every_write_route_has_a_cap_or_an_exemption():
    """The reverse drift pin (review #334 r5 I2): 'a new write entrance must
    add a BODY_CAPS line' lived only in body_size.py.md — this makes it
    executable. Uncovered AND unexempted write routes fail here, turning
    'forgot to think about the size story' into an explicit review decision."""
    write = {"POST", "PUT", "PATCH"}
    exempt = _NO_BODY_CAP_EXEMPT | _ENV_GATED_EXEMPT
    missing = []
    redundant = set()
    stale = set(_NO_BODY_CAP_EXEMPT)  # env-gated entries can't demand a live route
    for r in app.routes:
        methods = (getattr(r, "methods", set()) or set()) & write
        if not methods:
            continue  # GET/WS/Mount rows carry no request body to cap
        template = getattr(r, "path", "")
        sampled = _sampled(template)
        capped = any(
            (m & methods) and pat.match(sampled) for m, pat, _cap in BODY_CAPS
        )
        if capped:
            # a capped route makes its exemption dead weight (r6 M2: the
            # stale check must see BOTH rot directions, not just
            # route-disappeared)
            if template in exempt:
                redundant.add(template)
            continue
        stale.discard(template)
        if template not in exempt:
            missing.append(f"{sorted(methods)} {template}")
    assert not missing, (
        "write route(s) with neither a BODY_CAPS entry nor an explicit "
        f"exemption — decide their size story: {missing}"
    )
    assert not redundant, (
        f"exemptions for routes that now carry a BODY_CAPS entry — prune: "
        f"{sorted(redundant)}"
    )
    # an exemption with no matching UNCAPPED write route is dead weight —
    # the route disappeared, was renamed, or gained a cap above
    assert not stale, (
        f"exemptions matching no uncapped registered route: {sorted(stale)}"
    )


def test_chat_completions_declared_oversize_is_413_before_route():
    """Behavior pin for the env-gated cap (r6 I1): the /v1/chat/completions
    BODY_CAPS entry must fire in the middleware, before any route code —
    exercised on a bare app so it holds regardless of ENABLE_MANYFOLD_API."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.middleware.body_size import MAX_CHAT_COMPLETIONS_BYTES

    hits = []
    bare = FastAPI()
    bare.middleware("http")(body_size_middleware)

    @bare.post("/v1/chat/completions")
    async def _route():  # pragma: no cover - must not run
        hits.append(1)
        return {}

    client = TestClient(bare)
    r = client.post(
        "/v1/chat/completions",
        content=b"tiny",
        headers={
            "Content-Length": str(MAX_CHAT_COMPLETIONS_BYTES + 1),
            "Content-Type": "application/json",
        },
    )
    assert r.status_code == 413
    assert hits == []


def test_browser_policy_declared_oversize_is_rejected_before_route():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.middleware.body_size import MAX_BROWSER_SETTINGS_BYTES

    hits = []
    bare = FastAPI()
    bare.middleware("http")(body_size_middleware)

    @bare.put("/api/browser/policy/{agent_id}")
    @bare.post("/api/browser/policy/{agent_id}/revoke")
    async def route(agent_id: str):
        hits.append(agent_id)
        return {}

    with TestClient(bare) as client:
        for method, suffix in (("PUT", ""), ("POST", "/revoke")):
            path = f"/api/browser/policy/test-agent{suffix}"
            response = client.request(method, path, content=b"{}", headers={
                "Content-Length": str(MAX_BROWSER_SETTINGS_BYTES + 1),
            })
            assert response.status_code == 413
            assert hits == []
        assert client.put("/api/browser/policy/test-agent", json={}).status_code == 200
    assert hits == ["test-agent"]


@pytest.mark.parametrize("preference,value", [("source", "system"), ("mode", "headed")])
def test_browser_runtime_preference_declared_oversize_is_rejected_before_route(preference, value):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.middleware.body_size import MAX_BROWSER_SETTINGS_BYTES

    hits = []
    bare = FastAPI()
    bare.middleware("http")(body_size_middleware)
    path = f"/api/browser/runtime/{preference}"

    @bare.put(path)
    async def route():
        hits.append(preference)
        return {}

    with TestClient(bare) as client:
        response = client.put(path, content=b"{}", headers={
            "Content-Length": str(MAX_BROWSER_SETTINGS_BYTES + 1),
        })
        assert response.status_code == 413
        assert hits == []
        assert client.put(path, json={preference: value}).status_code == 200
    assert hits == [preference]


def test_middleware_order_cors_outermost_body_size_inside_access_log():
    names = [
        getattr(m.kwargs.get("dispatch"), "__name__", getattr(m.cls, "__name__", ""))
        for m in app.user_middleware
    ]
    # Existence first, all four (r6 M3): a missing layer must fail with the
    # actual stack in the message, not an opaque ValueError from .index.
    for n in (
        "CORSMiddleware",
        "access_log_middleware",
        "body_size_middleware",
        "auth_middleware",
    ):
        assert n in names, names
    # user_middleware is outermost-first. CORS must be outermost of the
    # four (constraint 2 in main.py — the one that WAS broken once: an
    # inner CORS never runs on early 401/413 returns, so cross-origin
    # callers get responses without ACAO and see only an opaque error).
    # Relative order only — a new outermost layer (e.g. tracing) is legal.
    cors, al, bs, au = (
        names.index("CORSMiddleware"),
        names.index("access_log_middleware"),
        names.index("body_size_middleware"),
        names.index("auth_middleware"),
    )
    assert cors < al < bs < au, names
