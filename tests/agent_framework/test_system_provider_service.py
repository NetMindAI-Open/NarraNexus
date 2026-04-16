"""
@file_name: test_system_provider_service.py
@author: Bin Liang
@date: 2026-04-16
@description: SystemProviderService env loading + is_enabled gating tests.

Verifies the service activates only when BOTH (a) the backend is in cloud
mode AND (b) all required SYSTEM_DEFAULT_LLM_* env vars are present. In
any other combination (local, partial env, missing key) it returns
is_enabled()==False so callers can short-circuit cleanly.
"""
import pytest

from xyz_agent_context.agent_framework.system_provider_service import (
    SystemProviderService,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    SystemProviderService._instance = None
    yield
    SystemProviderService._instance = None


def _set_cloud_env(monkeypatch, **kv):
    monkeypatch.setenv("DATABASE_URL", "mysql://u:p@h:3306/d")
    for k, v in kv.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)


def test_disabled_when_enabled_flag_unset(monkeypatch):
    _set_cloud_env(monkeypatch, SYSTEM_DEFAULT_LLM_ENABLED=None)
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is False


def test_disabled_in_local_mode_even_with_full_env(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DB_HOST", raising=False)
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_ENABLED", "true")
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_SOURCE", "netmind")
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_API_KEY", "sk-test")
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_AGENT_MODEL", "claude-sonnet-4-5")
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_EMBEDDING_MODEL", "BAAI/bge-m3")
    monkeypatch.setenv("SYSTEM_DEFAULT_LLM_HELPER_MODEL", "gpt-4o-mini")
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is False


def test_disabled_when_api_key_empty(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_LLM_ENABLED="true",
        SYSTEM_DEFAULT_LLM_API_KEY="",
        SYSTEM_DEFAULT_LLM_SOURCE="netmind",
        SYSTEM_DEFAULT_LLM_AGENT_MODEL="claude-sonnet-4-5",
        SYSTEM_DEFAULT_LLM_EMBEDDING_MODEL="BAAI/bge-m3",
        SYSTEM_DEFAULT_LLM_HELPER_MODEL="gpt-4o-mini",
    )
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is False


def test_disabled_when_slot_model_missing(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_LLM_ENABLED="true",
        SYSTEM_DEFAULT_LLM_API_KEY="sk-test",
        SYSTEM_DEFAULT_LLM_SOURCE="netmind",
        SYSTEM_DEFAULT_LLM_AGENT_MODEL="claude-sonnet-4-5",
        SYSTEM_DEFAULT_LLM_EMBEDDING_MODEL=None,
        SYSTEM_DEFAULT_LLM_HELPER_MODEL="gpt-4o-mini",
    )
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is False


def test_disabled_when_source_invalid(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_LLM_ENABLED="true",
        SYSTEM_DEFAULT_LLM_API_KEY="sk-test",
        SYSTEM_DEFAULT_LLM_SOURCE="not-a-real-source",
        SYSTEM_DEFAULT_LLM_AGENT_MODEL="claude-sonnet-4-5",
        SYSTEM_DEFAULT_LLM_EMBEDDING_MODEL="BAAI/bge-m3",
        SYSTEM_DEFAULT_LLM_HELPER_MODEL="gpt-4o-mini",
    )
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is False


def test_enabled_and_config_constructed_when_all_env_set(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_LLM_ENABLED="true",
        SYSTEM_DEFAULT_LLM_SOURCE="netmind",
        SYSTEM_DEFAULT_LLM_API_KEY="sk-test",
        SYSTEM_DEFAULT_LLM_ANTHROPIC_BASE_URL="https://api.netmind.ai/anthropic",
        SYSTEM_DEFAULT_LLM_OPENAI_BASE_URL="https://api.netmind.ai/openai/v1",
        SYSTEM_DEFAULT_LLM_AGENT_MODEL="claude-sonnet-4-5",
        SYSTEM_DEFAULT_LLM_EMBEDDING_MODEL="BAAI/bge-m3",
        SYSTEM_DEFAULT_LLM_HELPER_MODEL="gpt-4o-mini",
    )
    svc = SystemProviderService.instance()
    assert svc.is_enabled() is True
    cfg = svc.get_config()
    assert set(cfg.slots.keys()) == {"agent", "embedding", "helper_llm"}
    assert cfg.slots["agent"].model == "claude-sonnet-4-5"
    assert cfg.slots["embedding"].model == "BAAI/bge-m3"
    assert cfg.slots["helper_llm"].model == "gpt-4o-mini"
    keys = {p.api_key for p in cfg.providers.values()}
    assert keys == {"sk-test"}


def test_get_config_raises_when_disabled(monkeypatch):
    _set_cloud_env(monkeypatch, SYSTEM_DEFAULT_LLM_ENABLED=None)
    svc = SystemProviderService.instance()
    with pytest.raises(RuntimeError):
        svc.get_config()


def test_get_initial_quota_reads_env(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS="500000",
        SYSTEM_DEFAULT_QUOTA_OUTPUT_TOKENS="100000",
    )
    svc = SystemProviderService.instance()
    assert svc.get_initial_quota() == (500_000, 100_000)


def test_get_initial_quota_defaults_to_zero_when_unset(monkeypatch):
    _set_cloud_env(
        monkeypatch,
        SYSTEM_DEFAULT_QUOTA_INPUT_TOKENS=None,
        SYSTEM_DEFAULT_QUOTA_OUTPUT_TOKENS=None,
    )
    svc = SystemProviderService.instance()
    assert svc.get_initial_quota() == (0, 0)
