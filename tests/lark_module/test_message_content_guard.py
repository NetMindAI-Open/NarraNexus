"""
@file_name: test_message_content_guard.py
@date: 2026-08-04
@description: --text / --markdown values that look like file references are
rejected with an instructive error instead of being delivered literally.

Live incident (2026-08-04, claude_code x lark): the model's first
``--markdown <unquoted multi-word>`` was rejected by lark-cli, so it wrote
the reply to ``lark_reply.md`` and retried with ``--markdown
@lark_reply.md`` — an invented @file syntax these flags do not support.
lark-cli sent the literal string "@lark_reply.md" AND returned success, so
the human got garbage while the agent believed it delivered. The guard
turns that silent misdelivery into a correctable error; @-mentions and
ordinary @-containing prose must keep flowing.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.lark_module._lark_command_security import (
    sanitize_command,
    validate_command,
)

CHAT = "oc_0822639c9c2d42a5e2589a18d1ab4d2c"


@pytest.mark.parametrize("flag", ["--text", "--markdown"])
@pytest.mark.parametrize("ref", ["@lark_reply.md", "@notes.txt", "@./out/reply.md"])
def test_file_reference_content_is_rejected(flag, ref):
    with pytest.raises(ValueError, match="not read files"):
        sanitize_command(
            f"im +messages-send --chat-id {CHAT} {flag} {ref} --as bot"
        )


@pytest.mark.parametrize(
    "content",
    [
        "@张三 明天的会改到三点",  # mention + prose (quoted, has spaces)
        "@all 服务器已恢复",  # broadcast mention
        "@bob.smith 你好呀，请查收",  # dotted mention but with prose
        "普通消息，提到 file.md 这个文件名",  # filename inside prose
    ],
)
def test_mentions_and_prose_still_flow(content):
    args = sanitize_command(
        f'im +messages-send --chat-id {CHAT} --text "{content}" --as bot'
    )
    assert content in args


def test_bare_mention_without_extension_is_allowed():
    args = sanitize_command(
        f"im +messages-send --chat-id {CHAT} --text @all --as bot"
    )
    assert "@all" in args


def test_shell_substitution_hint_is_flag_appropriate():
    """The unexpandable-shell guard's recovery hint must not steer message
    flags toward @file — probe verified (2026-08-04) that --text/--markdown
    have NO @file expansion (a valid relative path still ships the literal
    string). Message flags get the inline hint; --content keeps @file."""
    with pytest.raises(ValueError) as e:
        sanitize_command(
            f'im +messages-send --chat-id {CHAT} --markdown "$(cat reply.md)" --as bot'
        )
    assert "inline" in str(e.value).lower()
    assert "--content @" not in str(e.value)

    with pytest.raises(ValueError) as e2:
        sanitize_command(
            'docs +update --doc-id D --content "$(cat report.md)" --as user'
        )
    assert "--content @" in str(e2.value)


@pytest.mark.parametrize("compound", ["--markdown=@lark_reply.md", "--text=@notes.txt"])
def test_equals_form_file_reference_is_also_rejected(compound):
    """`--flag=value` is ONE shlex token — the pairwise scan alone never
    sees it. Round-1 review reproduced the fake success through exactly
    this spelling; a model whose first spelling is rejected reaches for
    an equivalent one as a matter of course."""
    with pytest.raises(ValueError, match="not read files"):
        sanitize_command(
            f"im +messages-send --chat-id {CHAT} {compound} --as bot"
        )


def test_equals_form_command_substitution_is_rejected():
    """Same single-token blind spot for the 7/29 guard: the substitution
    hides after the '=' and used to sail through."""
    with pytest.raises(ValueError, match="shell"):
        sanitize_command(
            f'im +messages-send --chat-id {CHAT} --content="$(cat payload.json)" --as bot'
        )


def test_im_domain_hint_never_recommends_at_file():
    """NO im flag reads files (--content included — probe: lark-cli rejects
    `--content @payload.json` as invalid JSON). Every im rejection must
    give the inline advice, never the @file route."""
    for cmd in (
        f'im +messages-send --chat-id {CHAT} --content "$(cat payload.json)" --as bot',
        'im +messages-send --chat-id "$(cat id.txt)" --text hi --as bot',
    ):
        with pytest.raises(ValueError) as e:
            sanitize_command(cmd)
        assert "--content @" not in str(e.value), cmd
        assert "inline" in str(e.value).lower(), cmd


def test_mail_body_file_reference_is_rejected_with_the_real_route():
    """mail's --body is the same fake-success class (probe: nonexistent
    @file still ok:true, recipient gets the literal string). The guard
    covers it via the flag→file-route table, and the hint names the flag
    that ACTUALLY reads files (--body-file), not a nonexistent one."""
    with pytest.raises(ValueError) as e:
        sanitize_command(
            "mail +send --to a@b.c --subject S --body @body.html --as user"
        )
    assert "--body-file" in str(e.value)


def test_long_extension_file_reference_is_rejected():
    """'.markdown' is 8 chars — the old {1,5} regex let it straight
    through (round-3 review, probed)."""
    with pytest.raises(ValueError, match="not read files"):
        sanitize_command(
            f"im +messages-send --chat-id {CHAT} --markdown @reply.markdown --as bot"
        )


def test_bare_dotted_mention_is_not_a_file_reference():
    """'@bob.smith' (a pure mention, no prose) must flow — 'smith' is not
    a file extension. The extension whitelist fixes the old any-1-to-5-
    alphanumerics false positive."""
    args = sanitize_command(
        f"im +messages-send --chat-id {CHAT} --text @bob.smith --as bot"
    )
    assert "@bob.smith" in args


def test_positional_substitution_gets_no_empty_flag_route():
    """A $() with no preceding --flag (positional value) must fall to the
    generic --help-marker advice — round-4 review caught `_recovery_hint("")`
    fabricating a route with an EMPTY flag name (\"Pass the payload with
    ` @relative/path`\"), the exact指-a-nonexistent-road failure class."""
    with pytest.raises(ValueError) as e:
        sanitize_command(
            'docs +update --doc D --command overwrite "$(cat notes.md)"'
        )
    reason = str(e.value)
    assert "` @" not in reason
    assert "supports @file" in reason


@pytest.mark.parametrize("ref", ["@report.pdf", "@notes.log", "@reply.docx"])
def test_binary_and_log_extensions_are_also_rejected(ref):
    """Round-4: the whitelist traded the @bob.smith false positive for a
    coverage gap — these were caught by the old {1,5} rule and are all
    plausible write-then-reference names, none a Lark handle suffix."""
    with pytest.raises(ValueError, match="not read files"):
        sanitize_command(
            f"im +messages-send --chat-id {CHAT} --text {ref} --as bot"
        )


def test_docs_json_is_a_boolean_not_a_payload():
    """docs' --json is shorthand for --format json (boolean) — only
    base/record commands carry a payload --json. The stdin guard must not
    fire a fake `--json @file` route in docs (round-4 minor)."""
    allowed, _reason = validate_command("docs +create --title T --json -")
    assert allowed  # lark-cli itself rejects the stray '-' loudly


def test_base_json_stdin_still_rejected_with_real_route():
    with pytest.raises(ValueError) as e:
        sanitize_command(
            "base +record-batch-create --base-token T --table-id t --json - --as user"
        )
    assert "--json @" in str(e.value)


def test_reference_map_stdin_is_rejected():
    """--reference-map advertises '- reads stdin' in --help, but lark_cli
    never wires stdin — the empty-payload class the stdin guard exists
    for. Newly covered by the route table (round-4 minor)."""
    with pytest.raises(ValueError) as e:
        sanitize_command(
            "docs +update --doc D --content x --reference-map - --as user"
        )
    assert "--reference-map @" in str(e.value)


def test_docs_content_at_file_stays_permitted():
    """docs' --content @relative/path IS lark-cli's real file mechanism
    (the 7/29 hint's target) — the guard must not touch it. (docs v2 has
    no --text/--markdown at all; the CLI rejects them itself.)"""
    args = sanitize_command("docs +create --title T --content @./report.md --as user")
    assert "@./report.md" in args


def test_json_at_file_flags_are_untouched():
    """The @./file convention on --json flags is lark-cli's own feature —
    the guard only covers the literal-body message flags."""
    args = sanitize_command(
        "base +record-batch-create --base-token T --table-id t --json @./batch.json --as user"
    )
    assert "@./batch.json" in args
