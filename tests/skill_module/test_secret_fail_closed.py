"""
@file_name: test_secret_fail_closed.py
@date: 2026-08-13
@description: Skill env-config fail-closed behaviour (2026-08-01 incident).

When a stored credential was encrypted under a key that rotated/was lost, it
must NOT be injected as ciphertext into the skill's runtime env (which made the
skill run with garbage and fail opaquely). It is skipped, logged, and the skill
reports env_configured=False so the UI prompts a re-enter.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from narranexus_plugins.skill_module.skill_module import SkillModule
import narranexus.platform.marketplace._skill_marketplace_impl.secret_box as sb


@pytest.fixture
def key(monkeypatch):
    """Pin a known SecretBox key and reset the process-wide cache."""
    k = Fernet.generate_key().decode("ascii")
    monkeypatch.setenv("SKILL_SECRETS_KEY", k)
    monkeypatch.setattr(sb, "_default_box", None)
    return k


@pytest.fixture
def module(tmp_path):
    m = SkillModule(agent_id="agent_test", user_id="test_user")
    m.skills_dir = tmp_path / "skills"
    m.skills_dir.mkdir(parents=True, exist_ok=True)
    return m


def _make_skill(module, name, env_config):
    d = module.skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n")
    (d / ".skill_meta.json").write_text(json.dumps({"name": name, "env_config": env_config}))
    return d


def test_get_all_skill_env_vars_skips_undecryptable(module, key):
    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    _make_skill(module, "myskill", {"GOOD": box.encrypt("real-value"), "BAD": stranded})

    env = module.get_all_skill_env_vars()
    assert env.get("GOOD") == "real-value"
    assert "BAD" not in env  # ciphertext never injected — fail closed


def test_configured_env_var_names_excludes_undecryptable(key):
    # The single source of truth for "is this var configured": present + it
    # decrypts. A ciphertext under a lost key is NOT configured → drives
    # env_configured False everywhere (list/detail/MCP/hook/install).
    from narranexus_plugins.skill_module.skill_module import (
        configured_env_var_names,
    )

    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    env_config = {"GOOD": box.encrypt("real-value"), "BAD": stranded, "EMPTY": ""}

    configured = configured_env_var_names(env_config)
    assert configured == {"GOOD"}  # BAD undecryptable, EMPTY blank


def test_configured_env_var_names_excludes_empty_ciphertext(key):
    # "decryptable" is not "usable": encrypt("") is a real Fernet token that
    # decrypts to "". A scrubbed-bundle restore + legacy migration can leave
    # this shape; it must read as NOT configured (green-card-with-empty-key is
    # the exact 8/1 shape, just empty instead of ciphertext).
    from narranexus_plugins.skill_module.skill_module import (
        configured_env_var_names,
    )

    box = sb.get_secret_box()
    assert configured_env_var_names({"A": box.encrypt("")}) == set()


def test_env_configured_reflects_decryptability_incl_disabled(module, key):
    # env_configured is computed at the SkillModule source (_parse_skill_md),
    # so it is truthful for ENABLED and DISABLED skills alike (the helper takes
    # a raw env_config dict, never a name → no enabled-only path resolution).
    import json

    box = sb.get_secret_box()
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")

    # Enabled skill, credential intact → configured.
    good = module.skills_dir / "good"
    good.mkdir(parents=True)
    (good / "SKILL.md").write_text("---\nname: good\nrequires:\n  env:\n    - API_KEY\n---\n# good\n")
    (good / ".skill_meta.json").write_text(
        json.dumps({"name": "good", "requires": {"env": ["API_KEY"]},
                    "env_config": {"API_KEY": box.encrypt("v")}})
    )
    # Disabled skill, credential intact → must STAY configured (regression:
    # the old downgrade pass resolved by name and only saw enabled dirs, so it
    # flagged every disabled skill as needing re-entry).
    disabled = module.skills_dir / ".disabled" / "kept"
    disabled.mkdir(parents=True)
    (disabled / "SKILL.md").write_text("---\nname: kept\nrequires:\n  env:\n    - API_KEY\n---\n# kept\n")
    (disabled / ".skill_meta.json").write_text(
        json.dumps({"name": "kept", "requires": {"env": ["API_KEY"]},
                    "env_config": {"API_KEY": box.encrypt("v")}})
    )
    # Enabled skill whose credential is stranded → NOT configured.
    bad = module.skills_dir / "bad"
    bad.mkdir(parents=True)
    (bad / "SKILL.md").write_text("---\nname: bad\nrequires:\n  env:\n    - API_KEY\n---\n# bad\n")
    (bad / ".skill_meta.json").write_text(
        json.dumps({"name": "bad", "requires": {"env": ["API_KEY"]},
                    "env_config": {"API_KEY": stranded}})
    )

    by_name = {s.name: s for s in module.list_skills(include_disabled=True)}
    assert by_name["good"].env_configured is True
    assert by_name["kept"].env_configured is True  # disabled + intact → still green
    assert by_name["bad"].env_configured is False  # stranded → prompts re-enter


def test_needs_rewrite_migration_skipped_when_any_value_undecryptable(module, key):
    # A legacy-base64 value would normally be re-persisted (migrated). If the
    # SAME skill also has an undecryptable value, we must NOT rewrite the meta
    # — that would overwrite the still-encrypted ciphertext of the failed one
    # and destroy the last chance to recover it with the old key.
    import base64

    box = sb.get_secret_box()
    legacy = base64.b64encode(b"legacy-val").decode("ascii")
    stranded = Fernet(Fernet.generate_key()).encrypt(b"lost").decode("ascii")
    d = _make_skill(module, "myskill", {"LEG": legacy, "BAD": stranded})

    module.get_all_skill_env_vars()
    meta = json.loads((d / ".skill_meta.json").read_text())
    # LEG NOT migrated to Fernet — meta untouched because BAD failed.
    assert meta["env_config"]["LEG"] == legacy
    assert meta["env_config"]["BAD"] == stranded


def test_configured_env_var_names_is_total_on_bad_input(key):
    # A malformed meta (agent-writable file) must NOT crash the panel / drop
    # the whole agent's cred injection — every unreadable value degrades to
    # "not configured" (fail-closed), never propagates an exception (🟡2).
    from narranexus_plugins.skill_module.skill_module import (
        configured_env_var_names,
    )

    box = sb.get_secret_box()

    assert configured_env_var_names(None) == set()          # not a dict
    assert configured_env_var_names("nope") == set()         # not a dict
    assert configured_env_var_names({"A": 123}) == set()     # value not a str
    assert configured_env_var_names({"A": ""}) == set()      # blank
    # A good value still counts amid junk (per-var isolation).
    assert configured_env_var_names({"A": box.encrypt("v"), "B": 5}) == {"A"}


def test_configured_env_var_names_returns_empty_when_key_unavailable(monkeypatch):
    # An invalid SKILL_SECRETS_KEY makes get_secret_box() fail fast; the helper
    # must degrade to "nothing configured", not 500 the list.
    import narranexus.platform.marketplace._skill_marketplace_impl.secret_box as sbmod
    from narranexus_plugins.skill_module.skill_module import (
        configured_env_var_names,
    )

    monkeypatch.setattr(sbmod, "_default_box", None)
    monkeypatch.setenv("SKILL_SECRETS_KEY", "not-a-valid-fernet-key")
    assert configured_env_var_names({"A": "whatever"}) == set()


def test_get_all_skill_env_vars_survives_corrupt_meta_value(module, key):
    # 🟡2: a non-string stored value (agent-writable meta) must NOT crash the
    # injection path — it is skipped, and OTHER skills' credentials still inject.
    box = sb.get_secret_box()
    _make_skill(module, "corrupt", {"K": 123})            # non-str → skipped
    _make_skill(module, "healthy", {"GOOD": box.encrypt("real")})

    env = module.get_all_skill_env_vars()  # must not raise
    assert env.get("GOOD") == "real"
    assert "K" not in env


def test_get_all_skill_env_vars_empty_when_box_unavailable(module, key, monkeypatch):
    # 🟡2: a process-level key failure must fail CLOSED (inject nothing), not
    # raise out of gather and drop the agent's whole contribution.
    box = sb.get_secret_box()
    _make_skill(module, "healthy", {"GOOD": box.encrypt("real")})

    def _boom():
        raise ValueError("SKILL_SECRETS_KEY is set but is not a valid Fernet key")

    # Patch the PUBLIC seam, not the private module: `skill_module` reaches the
    # secret box through `narranexus.platform.marketplace.get_secret_box`
    # (batch 6c — a plugin package may not import another package's private
    # module). Patching `sb.get_secret_box` here left the real box in place and
    # the fail-closed path untested.
    import narranexus.platform.marketplace as marketplace

    monkeypatch.setattr(marketplace, "get_secret_box", _boom)
    assert module.get_all_skill_env_vars() == {}


def test_env_config_status_keeps_self_stored_platform_var(key):
    # 🟡1 at the source: a platform var that the user SELF-STORED (decryptable)
    # must be counted configured AND left OUT of platform_assumed, so the API
    # layer never downgrades it against the provider table.
    from narranexus_plugins.skill_module.skill_module import env_config_status

    box = sb.get_secret_box()
    env_config = {"NETMIND_API_KEY": box.encrypt("self-entered")}
    configured, assumed = env_config_status(["NETMIND_API_KEY"], env_config)
    assert configured is True
    assert assumed is None  # self-stored → not a platform assumption


def test_env_config_status_flags_platform_only_var_as_assumed(key):
    # A platform var with NO stored value is optimistically configured, but
    # recorded as platform_assumed so the API layer can DB-validate it.
    from narranexus_plugins.skill_module.skill_module import env_config_status

    configured, assumed = env_config_status(["NETMIND_API_KEY"], {})
    assert configured is True
    assert assumed == ["NETMIND_API_KEY"]


def test_env_config_status_non_platform_missing_var_is_unconfigured(key):
    from narranexus_plugins.skill_module.skill_module import env_config_status

    configured, assumed = env_config_status(["MY_KEY"], {})
    assert configured is False
    assert assumed is None


def _make_skill_with_frontmatter(module, name, requires_env, env_config):
    d = module.skills_dir / name
    d.mkdir(parents=True, exist_ok=True)
    env_lines = "\n".join(f"    - {v}" for v in requires_env)
    (d / "SKILL.md").write_text(f"---\nname: {name}\nrequires:\n  env:\n{env_lines}\n---\n# {name}\n")
    (d / ".skill_meta.json").write_text(
        json.dumps({"name": name, "requires": {"env": requires_env}, "env_config": env_config})
    )
    return d


def test_parse_wires_env_platform_assumed_into_skillinfo(module, key):
    # 🟡2: the parse → SkillInfo.env_platform_assumed → enrich seam. enrich's
    # ONLY input is this field, so _parse_skill_md must actually populate it —
    # asserted through the real list_skills → _parse_skill_md path, not a
    # hand-built SkillInfo.
    box = sb.get_secret_box()
    # Platform var, NO stored value → optimistically configured but flagged
    # assumed so the API layer DB-validates it.
    _make_skill_with_frontmatter(module, "platform-only", ["NETMIND_API_KEY"], {})
    # Same platform var, SELF-STORED → configured and NOT assumed (never
    # downgraded against the provider table).
    _make_skill_with_frontmatter(
        module, "self-stored", ["NETMIND_API_KEY"], {"NETMIND_API_KEY": box.encrypt("k")}
    )

    by_name = {s.name: s for s in module.list_skills(include_disabled=True)}
    assert by_name["platform-only"].env_configured is True
    assert by_name["platform-only"].env_platform_assumed == ["NETMIND_API_KEY"]
    assert by_name["self-stored"].env_configured is True
    assert by_name["self-stored"].env_platform_assumed is None


def test_parse_fallback_path_also_wires_env_platform_assumed(module, key):
    # The fallback return (SKILL.md present but WITHOUT frontmatter) must fill
    # the field too — the round-2 asymmetric-return bug lived here. requires_env
    # then comes only from .skill_meta.json.
    d = module.skills_dir / "no-frontmatter"
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text("# just a heading, no yaml frontmatter\n")
    (d / ".skill_meta.json").write_text(
        json.dumps({"name": "no-frontmatter", "requires": {"env": ["NETMIND_API_KEY"]}, "env_config": {}})
    )

    skill = {s.name: s for s in module.list_skills()}["no-frontmatter"]
    assert skill.env_configured is True
    assert skill.env_platform_assumed == ["NETMIND_API_KEY"]


def test_list_skills_survives_non_dict_meta(module, key):
    # 🟢5: a .skill_meta.json holding a JSON array (agent-writable) must not
    # crash _parse_skill_md / _scan_skills — the skill is still listed, other
    # skills unaffected.
    box = sb.get_secret_box()
    bad = module.skills_dir / "arraymeta"
    bad.mkdir(parents=True, exist_ok=True)
    (bad / "SKILL.md").write_text("---\nname: arraymeta\n---\n# arraymeta\n")
    (bad / ".skill_meta.json").write_text(json.dumps(["not", "a", "dict"]))
    _make_skill(module, "healthy", {"GOOD": box.encrypt("real")})

    names = {s.name for s in module.list_skills()}  # must not raise
    assert {"arraymeta", "healthy"} <= names
