"""
@file_name: gen_slack_skills.py
@author: NarraNexus
@date: 2026-05-08
@description: Generate per-method Slack Web API skill markdown from the pinned
              OpenAPI v2 spec.

Source spec : vendor/slack-api-specs/slack_web_openapi_v2.json
Upstream    : https://github.com/slackapi/slack-api-specs
Sourced from: bc08db49625630e3585bf2f1322128ea04f2a7f3   (web-api/slack_web_openapi_v2.json)

The vendored spec is byte-for-byte equal to the upstream snapshot at the
SHA above EXCEPT for one documentation example token redacted to a
placeholder ``"xoxb-EXAMPLE-EXAMPLE-EXAMPLE"``. The original upstream
value follows the ``xoxb-<digits>-<digits>-<base64>`` shape (see line
~19449 in upstream JSON, ``oauth.v2.access`` response example). It is
a Slack-published documentation example, not a real or revocable
token — but GitHub's secret scanner classifies that shape as a
possible leaked Slack bot token and refuses pushes containing it. Redacting the example is non-semantic: this generator only reads
structural definitions (paths / parameters / responses schemas) — it
NEVER reads the literal values inside ``examples``. So per-method md
output is identical with or without redaction.

The redaction is also defensively re-applied at re-download time via
``_redact_example_tokens`` so that the vendored file stays push-safe
even if someone refreshes from upstream. Note: upstream has not
updated this spec since 2020-10-06 (slackapi has effectively stopped
maintaining the OpenAPI v2 format), so refreshing is unlikely to be
needed.

The spec is OpenAPI v2 (Swagger). Each path looks like ``/chat.postMessage`` and
holds a single operation under ``post`` or ``get``. Args live in flat
``parameters`` (``in`` = ``formData`` | ``query`` | ``header``); there is no
``requestBody``.

Output layout::

    src/xyz_agent_context/module/slack_module/skills/
    ├── chat.postMessage.md
    ├── conversations.history.md
    ├── ...
    └── _index.json   # category -> [methods]

Filenames preserve the literal dotted method name (loader matches by exact
``Path.stem``). Header ``token`` parameters are dropped because the
SlackChannel injects auth itself.

Usage::

    uv run python scripts/gen_slack_skills.py
    uv run python scripts/gen_slack_skills.py --spec path/to/spec.json --out path/to/skills
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SPEC = REPO_ROOT / "vendor" / "slack-api-specs" / "slack_web_openapi_v2.json"
DEFAULT_OUT = (
    REPO_ROOT / "src" / "xyz_agent_context" / "module" / "slack_module" / "skills"
)

_HTML_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")

# Documentation example Slack tokens that look real enough to trip GitHub's
# secret scanner. ``_redact_example_tokens`` rewrites them in-place. This is
# defensive — applied at re-download time so the vendored spec stays
# push-safe even if a future maintainer refreshes from upstream. See module
# docstring for the rationale.
_REAL_LOOKING_XOXB = re.compile(r'"xoxb-\d+-\d+-[A-Za-z0-9]+"')

logger = logging.getLogger("gen_slack_skills")


def _redact_example_tokens(spec_text: str) -> str:
    """Replace realistic-looking ``xoxb-<digits>-<digits>-<base64>`` example
    tokens in raw spec JSON text with a generic placeholder. Other
    obvious-placeholder forms (``xoxa-access-token-string``, ``xoxp-1234``,
    ``xoxp-XXXX...``) are not touched — they don't trigger the scanner."""
    return _REAL_LOOKING_XOXB.sub('"xoxb-EXAMPLE-EXAMPLE-EXAMPLE"', spec_text)


def _strip_html(text: str) -> str:
    """Drop HTML tags from Slack descriptions; collapse runs of whitespace."""
    if not text:
        return ""
    plain = _HTML_TAG.sub("", text)
    plain = plain.replace("\r", "")
    plain = _WS.sub(" ", plain)
    return plain.strip()


def _extract_scopes(op: dict[str, Any]) -> str:
    security = op.get("security") or []
    scopes: list[str] = []
    for entry in security:
        for value in entry.values():
            if isinstance(value, list):
                scopes.extend(value)
    if not scopes:
        return "(varies — see Slack docs)"
    return ", ".join(f"`{s}`" for s in dict.fromkeys(scopes))


def _md_escape(cell: str) -> str:
    return cell.replace("|", "\\|").replace("\n", " ").strip()


def _arg_rows(parameters: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    rows: list[tuple[str, str, str, str]] = []
    for param in parameters:
        if param.get("in") == "header":
            continue  # Authorization headers are injected by the channel
        name = param.get("name") or ""
        if not name or name == "token":
            continue  # token is injected by the channel regardless of `in`
        type_ = param.get("type") or param.get("schema", {}).get("type") or "string"
        required = "yes" if param.get("required") else "no"
        desc = _strip_html(param.get("description") or "")
        rows.append((name, type_, required, desc))
    return rows


def _sample_args(rows: list[tuple[str, str, str, str]]) -> str:
    """Build a tiny example arg dict using only required fields with type stubs."""
    type_stubs = {
        "string": '"..."',
        "boolean": "true",
        "integer": "1",
        "number": "1",
        "array": "[]",
        "object": "{}",
    }
    required = [(n, t) for n, t, req, _ in rows if req == "yes"]
    if not required:
        return "{}"
    parts = [f'"{name}": {type_stubs.get(t, "...")}' for name, t in required]
    return "{" + ", ".join(parts) + "}"


def _render(method: str, op: dict[str, Any]) -> str:
    description = _strip_html(op.get("description") or "(no description)")
    scopes = _extract_scopes(op)
    rows = _arg_rows(op.get("parameters") or [])

    lines: list[str] = []
    lines.append(f"# {method}")
    lines.append("")
    lines.append("## Description")
    lines.append(description)
    lines.append("")
    lines.append("## Required scope")
    lines.append(scopes)
    lines.append("")
    lines.append("## Arguments")
    if not rows:
        lines.append("(no arguments)")
    else:
        lines.append("| name | type | required | description |")
        lines.append("|------|------|----------|-------------|")
        for name, type_, required, desc in rows:
            lines.append(
                f"| `{_md_escape(name)}` | {_md_escape(type_)} | {required} | {_md_escape(desc) or '—'} |"
            )
    lines.append("")
    lines.append("## Example")
    lines.append("```python")
    lines.append(f'slack_cli("{method}", {_sample_args(rows)})')
    lines.append("```")
    lines.append("")
    return "\n".join(lines)


def _is_skippable(op: dict[str, Any]) -> tuple[bool, str]:
    if not op.get("operationId"):
        return True, "missing operationId"
    if op.get("deprecated"):
        return True, "deprecated flag"
    tags = op.get("tags") or []
    if any("deprecated" in str(t).lower() for t in tags):
        return True, "deprecated tag"
    return False, ""


def generate(spec_path: Path, out_dir: Path) -> tuple[int, list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Defensive: if someone replaced the vendored spec with a fresh
    # upstream copy and forgot to re-redact, refuse to proceed so we
    # don't unintentionally commit a scanner-triggering token. Run the
    # generator after applying ``_redact_example_tokens`` to the spec
    # bytes first.
    raw_text = spec_path.read_text(encoding="utf-8")
    if _REAL_LOOKING_XOXB.search(raw_text):
        raise RuntimeError(
            f"Spec at {spec_path} contains a realistic-looking xoxb- "
            f"token in an example. GitHub's secret scanner will refuse a "
            f"push. Apply _redact_example_tokens() to the spec text "
            f"before writing it back. See module docstring."
        )
    spec = json.loads(raw_text)

    paths = spec.get("paths") or {}
    written = 0
    skipped: list[str] = []
    index: dict[str, list[str]] = defaultdict(list)

    for path, ops in sorted(paths.items()):
        method = path.lstrip("/")
        op = ops.get("post") or ops.get("get")
        if op is None:
            skipped.append(f"{method} (no get/post operation)")
            continue
        skip, reason = _is_skippable(op)
        if skip:
            skipped.append(f"{method} ({reason})")
            continue

        body = _render(method, op)
        (out_dir / f"{method}.md").write_text(body, encoding="utf-8")
        category = method.split(".", 1)[0]
        index[category].append(method)
        written += 1

    index_path = out_dir / "_index.json"
    index_payload = {cat: sorted(methods) for cat, methods in sorted(index.items())}
    index_path.write_text(
        json.dumps(index_payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return written, skipped


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Generate Slack Web API skill docs.")
    parser.add_argument("--spec", type=Path, default=DEFAULT_SPEC)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    if not args.spec.exists():
        logger.error("Spec not found: %s", args.spec)
        return 1

    written, skipped = generate(args.spec, args.out)
    logger.info("Wrote %d skill markdown files to %s", written, args.out)
    if skipped:
        logger.info("Skipped %d methods:", len(skipped))
        for entry in skipped:
            logger.info("  - %s", entry)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
