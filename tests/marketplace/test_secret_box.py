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

from xyz_agent_context._skill_marketplace_impl.secret_box import SecretBox


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
    box = SecretBox.load(key_dir=tmp_path)
    assert box.decrypt("not base64 !!!") == "not base64 !!!"


def test_env_config_lazy_migration(tmp_path):
    box = SecretBox.load(key_dir=tmp_path)
    legacy = {"API_KEY": base64.b64encode(b"abc").decode("ascii")}

    plain, needs_rewrite = box.decrypt_env_config(legacy)
    assert plain == {"API_KEY": "abc"}
    assert needs_rewrite is True

    encrypted = box.encrypt_env_config(plain)
    assert all(v.startswith(SecretBox.TOKEN_PREFIX) for v in encrypted.values())

    plain2, needs_rewrite2 = box.decrypt_env_config(encrypted)
    assert plain2 == plain
    assert needs_rewrite2 is False


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
