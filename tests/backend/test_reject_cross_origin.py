"""Unit tests for backend.auth.reject_cross_origin — the CSRF guard for
tokenless local-mode writes (marketplace publish). Covers the Origin: null
(sandboxed iframe) bypass and Sec-Fetch-Site defense-in-depth."""
import pytest
from fastapi import HTTPException

from backend.auth import reject_cross_origin


class _Req:
    def __init__(self, headers):
        self.headers = {k.lower(): v for k, v in headers.items()}


def _ok(headers):
    reject_cross_origin(_Req(headers))  # must NOT raise


def _blocked(headers):
    with pytest.raises(HTTPException) as e:
        reject_cross_origin(_Req(headers))
    assert e.value.status_code == 403


def test_no_origin_allowed():
    _ok({})  # CLI / same-origin


def test_loopback_origin_allowed():
    _ok({"Origin": "http://localhost:8000"})
    _ok({"Origin": "http://127.0.0.1:8000"})


def test_cross_site_origin_blocked():
    _blocked({"Origin": "https://evil.example.com"})


def test_origin_null_blocked():
    # Sandboxed iframe / data: form sends Origin: null — must be cross-origin.
    _blocked({"Origin": "null"})


def test_sec_fetch_site_cross_site_blocked():
    _blocked({"Sec-Fetch-Site": "cross-site"})


def test_sec_fetch_site_same_origin_allowed():
    _ok({"Sec-Fetch-Site": "same-origin"})
    _ok({"Sec-Fetch-Site": "none"})
