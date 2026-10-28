"""
@file_name: gen_theme_tokens.py
@author: Bin Liang
@date: 2026-09-03
@description: Extract every CSS custom property declared in frontend/src/index.css into themeTokens.generated.ts.

Run ``uv run python scripts/dev/gen_theme_tokens.py --write`` after changing
a design token. ``frontend/src/platform/__tests__/themeTokens.test.ts``
fails when the generated file is stale, so a new token cannot ship without
the theme allow-list learning it.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CSS = ROOT / "frontend" / "src" / "index.css"
OUT = ROOT / "frontend" / "src" / "platform" / "registries" / "themeTokens.generated.ts"
_TOKEN = re.compile(r"^\s*(--[a-zA-Z0-9-]+)\s*:")


def tokens() -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for line in CSS.read_text(encoding="utf-8").splitlines():
        m = _TOKEN.match(line)
        if m and m.group(1) not in seen:
            seen.add(m.group(1))
            out.append(m.group(1))
    return out


def render() -> str:
    body = "".join(f"  '{t}',\n" for t in tokens())
    return (
        "/**\n * @file_name: themeTokens.generated.ts\n * @author: Bin Liang\n * @date: 2026-09-03\n"
        " * @description: GENERATED from index.css by scripts/dev/gen_theme_tokens.py — the design tokens a theme may override. Do not edit.\n */\n"
        f"export const THEME_TOKENS = [\n{body}] as const;\n"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    text = render()
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        print(f"wrote {OUT} ({len(tokens())} tokens)")
        return 0
    if OUT.read_text(encoding="utf-8") != text:
        print("themeTokens.generated.ts is stale; run with --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
