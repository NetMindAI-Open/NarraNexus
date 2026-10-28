"""
@file_name: test_runner_steer.py
@author: Bin Liang
@date: 2026-08-21
@description: The runner's steer-line parser. Post-request stdin lines carry
live-steering injections as {"steer": {provider msg}}; a malformed or
non-steer line must be ignored (never take the turn down).
"""

from narranexus_plugins.frameworks_nexus_power.core.runner import (
    forward_steer_lines,
    parse_steer_line,
)


def test_valid_steer_line_returns_the_message():
    msg = parse_steer_line('{"steer": {"role": "user", "content": "hi"}}')
    assert msg == {"role": "user", "content": "hi"}


def test_non_steer_object_is_ignored():
    assert parse_steer_line('{"something_else": 1}') is None


def test_malformed_json_is_ignored():
    assert parse_steer_line("not json at all") is None
    assert parse_steer_line("") is None


def test_steer_that_is_not_an_object_is_ignored():
    assert parse_steer_line('{"steer": "just a string"}') is None
    assert parse_steer_line('{"steer": null}') is None


def test_forward_steer_lines_delivers_valid_and_skips_bad():
    # The daemon-thread body, tested directly: valid steer lines reach
    # `deliver`, malformed / non-steer lines are dropped, EOF ends it.
    delivered: list = []
    lines = [
        '{"steer": {"role": "user", "content": "a"}}\n',
        "garbage not json\n",
        '{"not_steer": 1}\n',
        '{"steer": {"role": "user", "content": "b"}}\n',
    ]
    forward_steer_lines(iter(lines), delivered.append)
    assert delivered == [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
    ]


def test_forward_steer_lines_returns_at_once_on_immediate_eof():
    # A non-steerable run's driver writes the request then closes stdin, so the
    # reader iterator is empty: the thread must return without delivering
    # anything (this is what makes it "zero behaviour change" and lets the
    # thread exit at once).
    delivered: list = []
    forward_steer_lines(iter([]), delivered.append)
    assert delivered == []
