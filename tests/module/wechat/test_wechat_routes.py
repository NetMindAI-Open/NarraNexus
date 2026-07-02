"""The WeChat bind route must not let a client steer the gateway host.

SSRF guard: /qrcode/poll previously accepted a client-supplied ``base_url`` that
the backend then fetched verbatim. The fix drops the field entirely — the host
is the fixed iLink default (or the baseurl the gateway returns at confirm time),
never a value the caller injects.
"""
from backend.routes.wechat import QrPollRequest


def test_poll_request_has_no_base_url_field():
    # The field is gone from the schema...
    assert "base_url" not in QrPollRequest.model_fields


def test_poll_request_ignores_client_supplied_base_url():
    # ...and an attempt to inject one (e.g. cloud metadata) is silently dropped
    # by pydantic rather than reaching the gateway client.
    req = QrPollRequest(
        agent_id="agent_1", qrcode="q", base_url="http://169.254.169.254/"
    )
    assert not hasattr(req, "base_url")
