"""
@file_name: test_sinks.py
@date: 2026-06-08
@description: NullSink swallows everything; FakeSink records for assertions.
"""
from xyz_agent_context.analytics.base import AnalyticsClient
from xyz_agent_context.analytics._impl.null_sink import NullSink
from xyz_agent_context.analytics._impl.fake_sink import FakeSink


def test_null_sink_is_a_client_and_noops():
    sink: AnalyticsClient = NullSink()
    sink.capture(distinct_id="u1", event="x", properties={"a": 1})
    sink.identify(distinct_id="u1", traits={"r": "z"})
    # No exception, nothing recorded — NullSink has no observable state.


def test_fake_sink_records_capture_and_identify():
    sink = FakeSink()
    sink.capture(distinct_id="u1", event="agent_created", properties={"agent_id": "a1"})
    sink.identify(distinct_id="u1", traits={"role": "individual"})
    assert sink.events == [("u1", "agent_created", {"agent_id": "a1"})]
    assert sink.identities == [("u1", {"role": "individual"})]
