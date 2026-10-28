"""
@file_name: test_secret_box.py
@author: NetMind.AI
@date: 2026-07-20
@description: Unit tests for SecretBox (Fernet encryption for skill env_config).

Covers: encrypt/decrypt roundtrip, legacy base64 fallback + lazy migration,
key-file generation with 0600 perms, key reuse across instances, and the
cloud-mode SKILL_SECRETS_KEY env var path.
"""

import base64
import stat

import pytest

from narranexus.platform.marketplace._skill_marketplace_impl.secret_box import (
    SecretBox,
    SecretDecryptError,
)


def test_encrypt_decrypt_roundtrip(tmp_path):
    box = SecretBox.load(key_dir=tmp_path)
    token = box.encrypt("sk-super-secret-123")
    assert token != "sk-super-secret-123"
    assert box.decrypt(token) == "sk-super-secret-123"


def test_decrypt_legacy_base64_value(tmp_path):
    box = SecretBox.load(key_dir=tmp_path)
    legacy = base64.b64encode("old-secret".encode("utf-8")).decode("ascii")
    assert box.decrypt(legacy) == "old-secret"


def test_decrypt_garbage_returns_value_unchanged(tmp_path):
    # A value that is neither a Fernet token nor legacy base64 is a genuinely
    # plain stored value — pass it through, don't raise (only a Fernet-shaped
    # token we cannot open is a real key-loss signal).
    box = SecretBox.load(key_dir=tmp_path)
    assert box.decrypt("not base64 !!!") == "not base64 !!!"


def test_decrypt_raises_on_undecryptable_fernet_token(tmp_path):
    # A token encrypted under a DIFFERENT key (the key rotated / was lost)
    # must FAIL CLOSED — never return the ciphertext, which a caller would
    # then run as if it were the credential (the 8/1 incident).
    from cryptography.fernet import Fernet

    box_a = SecretBox(Fernet.generate_key())
    box_b = SecretBox(Fernet.generate_key())
    token = box_a.encrypt("sk-real-secret")
    with pytest.raises(SecretDecryptError):
        box_b.decrypt(token)


def test_env_config_lazy_migration(tmp_path):
    box = SecretBox.load(key_dir=tmp_path)
    legacy = {"API_KEY": base64.b64encode(b"abc").decode("ascii")}

    plain, needs_rewrite, failed = box.decrypt_env_config(legacy)
    assert plain == {"API_KEY": "abc"}
    assert needs_rewrite is True
    assert failed == []

    encrypted = box.encrypt_env_config(plain)
    assert all(v.startswith(SecretBox.TOKEN_PREFIX) for v in encrypted.values())

    plain2, needs_rewrite2, failed2 = box.decrypt_env_config(encrypted)
    assert plain2 == plain
    assert needs_rewrite2 is False
    assert failed2 == []


def test_decrypt_env_config_excludes_and_reports_undecryptable_keys():
    # A mix of a good value and an undecryptable one: the good one decrypts,
    # the bad one is REPORTED (failed) and NEVER placed in `plain` as
    # ciphertext — so the caller can skip it instead of injecting garbage.
    from cryptography.fernet import Fernet

    box = SecretBox(Fernet.generate_key())
    stranded = SecretBox(Fernet.generate_key()).encrypt("was-under-the-lost-key")
    env = {"GOOD": box.encrypt("ok"), "BAD": stranded}

    plain, needs_rewrite, failed = box.decrypt_env_config(env)
    assert plain == {"GOOD": "ok"}  # BAD excluded — no ciphertext leaks through
    assert failed == ["BAD"]
    assert "BAD" not in plain


def test_decrypt_env_config_is_total_on_malformed_input(tmp_path):
    # decrypt_env_config feeds both the status query and the injection path a
    # raw, agent-writable meta. It must be TOTAL: a non-dict env yields empty
    # results, and a non-string value is reported as `failed` (never raises
    # deep in decrypt).
    box = SecretBox.load(key_dir=tmp_path)

    assert box.decrypt_env_config("not a dict") == ({}, False, [])
    assert box.decrypt_env_config(None) == ({}, False, [])

    plain, needs_rewrite, failed = box.decrypt_env_config(
        {"GOOD": box.encrypt("v"), "BAD": 123, "BLANK": ""}
    )
    assert plain == {"GOOD": "v"}  # BAD non-str skipped, BLANK absent
    assert failed == ["BAD"]       # corrupt value reported for a re-enter prompt
    assert needs_rewrite is False


def test_key_file_created_with_0600(tmp_path):
    SecretBox.load(key_dir=tmp_path)
    key_file = tmp_path / "skill_secrets.key"
    assert key_file.exists()
    assert stat.S_IMODE(key_file.stat().st_mode) == 0o600


def test_key_reused_across_instances(tmp_path):
    first = SecretBox.load(key_dir=tmp_path)
    token = first.encrypt("value")
    second = SecretBox.load(key_dir=tmp_path)
    assert second.decrypt(token) == "value"


def test_env_var_key_takes_precedence(monkeypatch, tmp_path):
    from cryptography.fernet import Fernet

    monkeypatch.setenv("SKILL_SECRETS_KEY", Fernet.generate_key().decode("ascii"))
    box = SecretBox.load(key_dir=tmp_path)
    assert not (tmp_path / "skill_secrets.key").exists()
    assert box.decrypt(box.encrypt("y")) == "y"


def test_invalid_env_var_key_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("SKILL_SECRETS_KEY", "definitely-not-a-fernet-key")
    with pytest.raises(ValueError):
        SecretBox.load(key_dir=tmp_path)
