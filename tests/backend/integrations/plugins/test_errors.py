"""
@file_name: test_errors.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests mapping raw subprocess failures to a structured,
              bilingual PluginError the frontend can render directly.
"""
from __future__ import annotations

from backend.integrations.plugins._installers.base import PluginInstallSubprocessError
from backend.integrations.plugins.errors import PluginError, classify_error


def _subprocess_error(output: str, returncode: int = 1) -> PluginInstallSubprocessError:
    return PluginInstallSubprocessError(cmd=["npm", "install"], returncode=returncode, output=output)


def test_classifies_registry_timeout_as_registry_slow():
    exc = _subprocess_error("npm ERR! network timeout at: https://registry.npmjs.org/@anthropic-ai%2fclaude-code")
    error = classify_error(exc)
    assert isinstance(error, PluginError)
    assert error.kind == "registry_slow"
    assert "npmmirror" in error.message or "pip" in error.message


def test_classifies_eacces_as_permission_denied():
    exc = _subprocess_error("EACCES: permission denied, mkdir '/home/user/.narranexus/plugins/nodejs'")
    error = classify_error(exc)
    assert error.kind == "permission_denied"


def test_classifies_dns_failure_as_network_blocked():
    exc = _subprocess_error("npm ERR! request to https://registry.npmjs.org failed, reason: getaddrinfo ENOTFOUND registry.npmjs.org")
    error = classify_error(exc)
    assert error.kind == "network_blocked"


def test_unrecognized_output_falls_back_to_unknown():
    exc = _subprocess_error("some completely unrelated failure message")
    error = classify_error(exc)
    assert error.kind == "unknown"


def test_classify_error_handles_plain_exceptions_too():
    error = classify_error(FileNotFoundError("npm not found"))
    assert error.kind == "unknown"
    assert error.message
