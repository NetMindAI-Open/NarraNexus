"""
@file_name: test_command_escape_expansion.py
@date: 2026-04-20
@description: Lock the contract that sanitize_command expands literal \\n / \\t / \\r
              escape sequences inside quoted arg values so Lark renders line breaks
              correctly when agents compose markdown commands.
"""

from xyz_agent_context.module.lark_module._lark_command_security import (
    sanitize_command,
)


def test_markdown_arg_expands_literal_newline():
    args = sanitize_command(
        'im +messages-send --chat-id oc_xxx --markdown "hi\\nworld"'
    )
    # The --markdown value should contain a REAL newline, not the two-char
    # sequence "\n". Lark's markdown renderer relies on real newlines.
    idx = args.index("--markdown")
    assert args[idx + 1] == "hi\nworld"


def test_text_arg_expands_multiple_escapes():
    args = sanitize_command(
        'im +messages-send --chat-id oc_xxx --text "a\\nb\\tc\\rd"'
    )
    idx = args.index("--text")
    assert args[idx + 1] == "a\nb\tc\rd"


def test_non_markdown_args_unaffected_by_escape_expansion():
    # chat-id / flag names etc. do not contain backslashes so expansion is a no-op.
    args = sanitize_command("im +messages-send --chat-id oc_xxx --text hello")
    assert "--chat-id" in args
    assert "oc_xxx" in args
    assert "hello" in args


def test_already_real_newlines_passthrough():
    # If somehow the caller passes a real newline, it survives unchanged.
    args = sanitize_command('im +messages-send --chat-id oc_xxx --markdown "a\nb"')
    idx = args.index("--markdown")
    assert args[idx + 1] == "a\nb"
