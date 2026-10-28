"""
@file_name: test_event_log_surrogates.py
@author: Bin Liang
@date: 2026-09-11
@description: The NDJSON event log must never raise on a lone surrogate in
a payload — a log write that throws kills the turn (prod 2026-09-11:
NarraMessenger replies died with "surrogates not allowed").
"""

import json

import pytest

from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.event_log import (
    FileEventLogWriter,
    ndjson_line,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.events import LoopEvent


def test_ndjson_line_is_utf8_encodable_with_surrogates():
    row = {"payload": {"text": "hi 👋 and lone \ud83d"}}
    line = ndjson_line(row)
    line.encode("utf-8")
    assert json.loads(line)["payload"]["text"] == "hi 👋 and lone �"


def test_ndjson_line_keeps_non_ascii_verbatim():
    assert ndjson_line({"t": "中文 👋"}) == '{"t": "中文 👋"}'


@pytest.mark.asyncio
async def test_file_writer_survives_lone_surrogate(tmp_path):
    path = tmp_path / "t.ndjson"
    writer = FileEventLogWriter("th", str(path))
    event = LoopEvent(seq=1, track="ui", type="tool_arg_delta", payload={"text": "👋\ud83d"})
    await writer.append(event)
    await writer.flush()
    row = json.loads(path.read_text(encoding="utf-8"))
    assert row["payload"]["text"] == "👋�"
