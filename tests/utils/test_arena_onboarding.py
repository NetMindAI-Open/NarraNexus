"""
@file_name: test_arena_onboarding.py
@author: Bin Liang
@date: 2026-06-15
@description: Unit tests for ArenaOnboarder — name generation, the register
              409-retry path (with a fake HTTP client, no network), and the
              workspace file layout that the runtime SkillModule must consume.
"""

import base64
import json
import random
from pathlib import Path

import pytest

from xyz_agent_context.utils.arena_onboarding import (
    ArenaOnboarder,
    ArenaCredentials,
    ArenaApiError,
    GROUP_TEMPERAMENT,
    GROUP_FORCE,
    GROUP_CREATURE,
)
from xyz_agent_context.utils.schema_registry import get_registered_tables


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _FakeClient:
    """Scripts a sequence of register responses; records the names + URLs attempted."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.attempted_names = []
        self.attempted_urls = []
        self.attempted_bodies = []
        self.attempted_headers = []

    def post(self, url, headers=None, json=None, **kw):
        self.attempted_names.append((json or {}).get("name"))
        self.attempted_urls.append(url)
        self.attempted_bodies.append(json or {})
        self.attempted_headers.append(headers or {})
        return self._responses.pop(0)

    def get(self, url, headers=None, **kw):
        return _FakeResponse(200, {"id": "agent_x", "status": "active"})

    def close(self):
        pass


def _ok(name="Brave_Frost_Fox"):
    return _FakeResponse(201, {
        "agent": {"id": "agent_ABC123", "name": name},
        "credentials": {"api_key": "arena_sk_realkey", "claim_token": "arena_claim_tok"},
    })


def _taken():
    return _FakeResponse(409, {"error": "Agent name already taken", "code": "NAME_TAKEN"})


def test_generate_name_uses_three_groups_and_underscores():
    onb = ArenaOnboarder(rng=random.Random(42))
    name = onb.generate_name()
    parts = name.split("_")
    assert len(parts) == 3
    assert parts[0] in GROUP_TEMPERAMENT
    assert parts[1] in GROUP_FORCE
    assert parts[2] in GROUP_CREATURE
    # alphanumeric + underscore only (Arena's rule)
    assert all(c.isalnum() or c == "_" for c in name)


def test_register_retries_on_409_then_succeeds():
    client = _FakeClient([_taken(), _taken(), _ok()])
    onb = ArenaOnboarder(http_client=client, rng=random.Random(1))
    creds = onb.register()
    assert creds.api_key == "arena_sk_realkey"
    assert creds.agent_id == "agent_ABC123"
    assert creds.claim_token == "arena_claim_tok"
    # claim_url derived from the token
    assert creds.claim_url.endswith("arena_claim_tok")
    assert len(client.attempted_names) == 3  # two 409s then a 201


def test_register_explicit_name_raises_on_409():
    client = _FakeClient([_taken()])
    onb = ArenaOnboarder(http_client=client)
    with pytest.raises(ArenaApiError):
        onb.register(name="Already_Taken_Name")


def test_install_skill_writes_files_consumable_by_skillmodule(tmp_path: Path):
    onb = ArenaOnboarder(http_client=_FakeClient([]))
    creds = ArenaCredentials(
        api_key="arena_sk_xyz",
        agent_id="agent_ARENA",
        agent_name="Swift_Nova_Wolf",
        claim_token="arena_claim_9",
        claim_url="https://arena42.ai/claim/arena_claim_9",
        referral_code="REF-ABC",
    )
    skills_dir = tmp_path / "agent_1_user_1" / "skills"
    result = onb.install_skill(
        skills_dir, creds, skill_md="# Arena\n", owner_user_id="user_1"
    )

    sd = result.skill_dir
    assert sd.name == "arena"
    assert set(result.files_written) == {
        "SKILL.md", ".skill_meta.json", "credentials.json", "arena_profile.json"
    }

    # .skill_meta.json env_config is base64 and decodes to the real values
    meta = json.loads((sd / ".skill_meta.json").read_text())
    env = meta["env_config"]
    assert base64.b64decode(env["ARENA_API_KEY"]).decode() == "arena_sk_xyz"
    assert base64.b64decode(env["ARENA_AGENT_ID"]).decode() == "agent_ARENA"
    assert "ARENA_API_URL" in env and "ARENA_SKILL_VERSION" in env

    # credentials.json carries the claim_token and is chmod 0600
    cj = json.loads((sd / "credentials.json").read_text())
    assert cj["claim_token"] == "arena_claim_9"
    assert (sd / "credentials.json").stat().st_mode & 0o777 == 0o600

    # arena_profile.json is the non-secret record
    pj = json.loads((sd / "arena_profile.json").read_text())
    assert pj["referral_code"] == "REF-ABC"
    assert pj["owner_user_id"] == "user_1"
    assert pj["provisioned_by"] == "NarraNexus"
    # api_key must NOT leak into the non-secret profile
    assert "arena_sk_xyz" not in (sd / "arena_profile.json").read_text()


def test_register_targets_configured_api_base():
    # The configured api_base (e.g. the Arena dev env) is where registration
    # goes — not the hard-coded prod default. This is what keeps dev agents off
    # the live ladder.
    client = _FakeClient([_ok()])
    onb = ArenaOnboarder(
        api_base="https://arena-dev-api.protago-dev.com", http_client=client
    )
    onb.register()
    assert client.attempted_urls == [
        "https://arena-dev-api.protago-dev.com/api/v1/agents/register"
    ]


def test_install_skill_api_url_follows_api_base(tmp_path: Path):
    # The agent's runtime calls read ARENA_API_URL from the installed skill env;
    # it must match the api_base the agent registered against, so a dev agent
    # calls the dev Arena, never prod.
    onb = ArenaOnboarder(
        api_base="https://arena-dev-api.protago-dev.com", http_client=_FakeClient([])
    )
    creds = ArenaCredentials(
        api_key="k", agent_id="a", agent_name="Swift_Nova_Wolf",
        claim_token="t", claim_url="u", referral_code=None,
    )
    result = onb.install_skill(
        tmp_path / "skills", creds, skill_md="# Arena\n", owner_user_id="user_1"
    )
    meta = json.loads((result.skill_dir / ".skill_meta.json").read_text())
    api_url = base64.b64decode(meta["env_config"]["ARENA_API_URL"]).decode()
    assert api_url == "https://arena-dev-api.protago-dev.com"


def test_provisioning_uses_settings_arena_api_base(monkeypatch):
    # The provisioning service must build the onboarder with the configured base
    # (settings.arena_api_base), not ArenaOnboarder's hard-coded default — this
    # is the single wiring line that binds dev provisioning to the dev Arena.
    import asyncio

    from xyz_agent_context.services import arena_provisioning_service as svc

    monkeypatch.setattr(svc.settings, "arena_api_base", "https://arena-dev-api.protago-dev.com")

    captured = {}

    class _CapturingOnboarder:
        def __init__(self, *, api_base, **kw):
            captured["api_base"] = api_base

        def register(self, *a, **kw):
            raise RuntimeError("stop after construction")

        def close(self):
            pass

    monkeypatch.setattr(svc, "ArenaOnboarder", _CapturingOnboarder)

    class _NoAgents:
        def __init__(self, db):
            pass

        async def find(self, *a, **kw):
            return []

    # provision() imports AgentRepository locally from its source module.
    import xyz_agent_context.repository.agent_repository as agent_repo_mod
    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _NoAgents)

    service = svc.ArenaProvisioningService(db_client=object())
    with pytest.raises(RuntimeError, match="stop after construction"):
        asyncio.run(service.provision("user_x"))
    assert captured["api_base"] == "https://arena-dev-api.protago-dev.com"


# ── bind_owner: platform-only owner-email binding (no email round-trip) ──────


def _bind_creds():
    return ArenaCredentials(
        api_key="arena_sk_bindkey",
        agent_id="agent_BIND",
        agent_name="Swift_Nova_Wolf",
    )


def test_bind_owner_success_targets_endpoint_with_key_and_token():
    # The platform-bind endpoint authenticates with the agent's api_key and
    # carries the user's NetMind JWT in the body (no Bearer prefix).
    client = _FakeClient([
        _FakeResponse(200, {"message": "Owner email bound successfully",
                            "email": "user@example.com"})
    ])
    onb = ArenaOnboarder(
        api_base="https://arena-dev-api.protago-dev.com", http_client=client
    )
    result = onb.bind_owner(_bind_creds(), "netmind_jwt_xyz")

    assert result["status"] == "bound"
    assert result["email"] == "user@example.com"
    assert client.attempted_urls == [
        "https://arena-dev-api.protago-dev.com/api/v1/agents/me/platform-bind-owner"
    ]
    assert client.attempted_headers[0]["Authorization"] == "Bearer arena_sk_bindkey"
    assert client.attempted_bodies[0] == {"user_token": "netmind_jwt_xyz"}


def test_bind_owner_no_email_on_record_is_skipped_not_error():
    client = _FakeClient([
        _FakeResponse(200, {"message": "No email on record, binding skipped"})
    ])
    onb = ArenaOnboarder(http_client=client)
    result = onb.bind_owner(_bind_creds(), "tok")
    assert result["status"] == "skipped_no_email"
    assert result["email"] is None


def test_bind_owner_already_bound_treated_as_success():
    client = _FakeClient([
        _FakeResponse(400, {"code": "EMAIL_ALREADY_BOUND",
                            "message": "already bound"})
    ])
    onb = ArenaOnboarder(http_client=client)
    result = onb.bind_owner(_bind_creds(), "tok")
    assert result["status"] == "already_bound"


def test_bind_owner_invalid_token():
    client = _FakeClient([
        _FakeResponse(401, {"code": "INVALID_TOKEN", "message": "bad"})
    ])
    onb = ArenaOnboarder(http_client=client)
    result = onb.bind_owner(_bind_creds(), "tok")
    assert result["status"] == "invalid_token"


def test_bind_owner_rate_limited():
    client = _FakeClient([_FakeResponse(429, {"message": "slow down"})])
    onb = ArenaOnboarder(http_client=client)
    result = onb.bind_owner(_bind_creds(), "tok")
    assert result["status"] == "rate_limited"


def test_bind_owner_missing_api_key_never_calls_network():
    client = _FakeClient([])  # empty: a network call would IndexError
    onb = ArenaOnboarder(http_client=client)
    creds = ArenaCredentials(api_key=None, agent_id="a", agent_name="n")
    result = onb.bind_owner(creds, "tok")
    assert result["status"] == "error"
    assert client.attempted_urls == []


def test_bind_owner_blank_token_never_calls_network():
    client = _FakeClient([])
    onb = ArenaOnboarder(http_client=client)
    result = onb.bind_owner(_bind_creds(), "")
    assert result["status"] == "error"
    assert client.attempted_urls == []


def _stub_cold_path(monkeypatch, svc, fake_onboarder_cls):
    """Monkeypatch provision()'s collaborators so only the bind wiring is live."""
    monkeypatch.setattr(svc, "ArenaOnboarder", fake_onboarder_cls)

    captured = {}

    class _Repo:
        def __init__(self, db):
            pass

        async def find(self, *a, **kw):
            return []

        async def add_agent(self, **kw):
            captured["metadata"] = kw["agent_metadata"]

        async def update_agent(self, *a, **kw):
            pass

    import xyz_agent_context.repository.agent_repository as agent_repo_mod
    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _Repo)

    class _IF:
        def __init__(self, db):
            pass

        async def create_agent_level_instances(self, agent_id):
            pass

    import xyz_agent_context.module._module_impl.instance_factory as if_mod
    monkeypatch.setattr(if_mod, "InstanceFactory", _IF)

    import xyz_agent_context.utils.workspace_paths as wp_mod
    monkeypatch.setattr(wp_mod, "agent_workspace_path", lambda *a, **kw: Path("/tmp/nx_ws"))

    async def _noop_aw(self, *a, **kw):
        pass

    async def _noop_jobs(self, *a, **kw):
        return []

    async def _noop_bootstrap(*a, **kw):
        pass

    monkeypatch.setattr(svc.ArenaProvisioningService, "_set_awareness", _noop_aw)
    monkeypatch.setattr(svc.ArenaProvisioningService, "_create_paused_jobs", _noop_jobs)
    monkeypatch.setattr(svc, "apply_bootstrap", _noop_bootstrap)
    return captured


def test_provision_cold_path_binds_owner_and_records_status(monkeypatch):
    import asyncio
    from xyz_agent_context.services import arena_provisioning_service as svc

    seen = {}

    class _FakeOnboarder:
        def __init__(self, *, api_base, **kw):
            pass

        def register(self, *a, **kw):
            return svc.ArenaCredentials(
                api_key="k", agent_id="arena_1", agent_name="Brave_Frost_Fox"
            )

        def bind_owner(self, creds, token):
            seen["token"] = token
            seen["key"] = creds.api_key
            return {"status": "bound", "email": "u@e.com"}

        def install_skill(self, *a, **kw):
            pass

        def close(self):
            pass

    captured = _stub_cold_path(monkeypatch, svc, _FakeOnboarder)
    service = svc.ArenaProvisioningService(db_client=object())
    result = asyncio.run(service.provision("user_x", user_token="netmind_jwt"))

    assert seen["token"] == "netmind_jwt"  # NetMind JWT forwarded to bind
    assert seen["key"] == "k"              # authenticated with the agent api_key
    assert captured["metadata"]["arena_owner_bind"] == "bound"  # recorded
    assert result["owner_bind"] == "bound"


def test_provision_cold_path_without_token_skips_bind(monkeypatch):
    import asyncio
    from xyz_agent_context.services import arena_provisioning_service as svc

    seen = {"bind_called": False}

    class _FakeOnboarder:
        def __init__(self, *, api_base, **kw):
            pass

        def register(self, *a, **kw):
            return svc.ArenaCredentials(api_key="k", agent_id="a", agent_name="Brave_Frost_Fox")

        def bind_owner(self, creds, token):
            seen["bind_called"] = True
            return {"status": "bound"}

        def install_skill(self, *a, **kw):
            pass

        def close(self):
            pass

    captured = _stub_cold_path(monkeypatch, svc, _FakeOnboarder)
    service = svc.ArenaProvisioningService(db_client=object())
    result = asyncio.run(service.provision("user_x"))  # no token

    assert seen["bind_called"] is False
    assert captured["metadata"]["arena_owner_bind"] == "no_token"
    assert result["owner_bind"] == "no_token"


def test_provision_warm_path_retries_bind_from_workspace_key(monkeypatch, tmp_path):
    import asyncio
    from types import SimpleNamespace
    from xyz_agent_context.services import arena_provisioning_service as svc

    # An already-provisioned agent whose first bind was skipped (no email then).
    existing = SimpleNamespace(
        agent_id="agent_OLD",
        agent_name="Brave_Frost_Fox",
        agent_metadata={
            "provisioned_source": "arena",
            "arena_agent_id": "arena_1",
            "arena_agent_name": "Brave_Frost_Fox",
            "arena_owner_bind": "skipped_no_email",
        },
    )
    updates = {}

    class _Repo:
        def __init__(self, db):
            pass

        async def find(self, *a, **kw):
            return [existing]

        async def update_agent(self, agent_id, upd):
            updates["agent_id"] = agent_id
            updates["meta"] = upd["agent_metadata"]

    import xyz_agent_context.repository.agent_repository as agent_repo_mod
    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _Repo)

    # The api_key lives only in the workspace credentials.json — lay one down.
    skill_dir = tmp_path / "skills" / "arena"
    skill_dir.mkdir(parents=True)
    (skill_dir / "credentials.json").write_text(
        json.dumps({"api_key": "arena_sk_fromfile", "agent_id": "arena_1"})
    )
    import xyz_agent_context.utils.workspace_paths as wp_mod
    monkeypatch.setattr(wp_mod, "agent_workspace_path", lambda *a, **kw: tmp_path)

    seen = {}

    class _FakeOnboarder:
        def __init__(self, *, api_base, **kw):
            pass

        def bind_owner(self, creds, token):
            seen["key"] = creds.api_key
            seen["token"] = token
            return {"status": "bound", "email": "u@e.com"}

        def close(self):
            pass

    monkeypatch.setattr(svc, "ArenaOnboarder", _FakeOnboarder)

    service = svc.ArenaProvisioningService(db_client=object())
    result = asyncio.run(service.provision("user_x", user_token="netmind_jwt"))

    assert result["reused"] is True
    assert seen["key"] == "arena_sk_fromfile"  # key read back from workspace
    assert seen["token"] == "netmind_jwt"
    assert result["owner_bind"] == "bound"
    assert updates["meta"]["arena_owner_bind"] == "bound"  # persisted


def test_provision_warm_path_already_bound_skips_network(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from xyz_agent_context.services import arena_provisioning_service as svc

    existing = SimpleNamespace(
        agent_id="agent_OLD",
        agent_name="Brave_Frost_Fox",
        agent_metadata={
            "provisioned_source": "arena",
            "arena_agent_id": "arena_1",
            "arena_agent_name": "Brave_Frost_Fox",
            "arena_owner_bind": "bound",
        },
    )

    class _Repo:
        def __init__(self, db):
            pass

        async def find(self, *a, **kw):
            return [existing]

    import xyz_agent_context.repository.agent_repository as agent_repo_mod
    monkeypatch.setattr(agent_repo_mod, "AgentRepository", _Repo)

    class _BoomOnboarder:
        def __init__(self, *a, **kw):
            raise AssertionError("warm path must not hit Arena when already bound")

    monkeypatch.setattr(svc, "ArenaOnboarder", _BoomOnboarder)

    service = svc.ArenaProvisioningService(db_client=object())
    result = asyncio.run(service.provision("user_x", user_token="netmind_jwt"))
    assert result["owner_bind"] == "bound"


def test_arena_awareness_has_confidentiality_rule():
    # 铁律 #4 home: the concrete "never leak to competitors" rule lives in the
    # Arena persona, naming the adversarial threat.
    from xyz_agent_context.services.arena_provisioning_service import ARENA_AWARENESS

    text = ARENA_AWARENESS.lower()
    assert "confidentiality" in text
    assert "competitor" in text
    assert "arena_api_key" in text or "credential" in text


def test_generic_awareness_has_confidentiality_rule():
    # Defense-in-depth: the generic confidentiality principle is in the awareness
    # instruction template, so every agent (incl. already-provisioned ones) gets
    # it live, with no scenario naming (铁律 #4: generic stays generic).
    from xyz_agent_context.module.awareness_module.prompts import (
        AWARENESS_MODULE_INSTRUCTIONS,
    )

    text = AWARENESS_MODULE_INSTRUCTIONS.lower()
    assert "confidential" in text
    assert "creator" in text
    # generic layer must not hard-code the Arena scenario
    assert "arena" not in text


def test_no_arena_credentials_table():
    # Arena is an external service: we deliberately keep NO credentials table.
    # The api_key lives only in the agent workspace; idempotency keys on the
    # agents table (agent_metadata.provisioned_source).
    tables = {t.name for t in get_registered_tables()}
    assert "arena_credentials" not in tables
