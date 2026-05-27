"""
@file_name: mock_manyfold_adapter.py
@author: NexusAgent
@date: 2026-05-25
@description: Mock Manyfold platform adapter for protocol contract testing

Mimics the behaviour of Manyfold's real ApiChatAdapter
(``apps/api/src/modules/chat/adapters/openclaw.adapter.ts:90-328``) so
we can verify our /v1/chat/completions endpoint matches the platform's
expectations WITHOUT needing the full Manyfold stack running.

Each assertion in tests below cites the source-of-truth Manyfold file:line
that motivates it.

Usage:
    python -m tests.e2e.mock_manyfold_adapter \\
        --base-url http://127.0.0.1:8000 \\
        --token <MANYFOLD_GATEWAY_TOKEN> \\
        --agent-id <agent_id> \\
        --prompt "hello"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from typing import Optional

import httpx


@dataclass
class AdapterResult:
    preflight_ok: bool = False
    preflight_status: int = 0
    chat_ok: bool = False
    chat_status: int = 0
    chunks_received: int = 0
    content_chars: int = 0
    full_content: str = ""
    saw_role_chunk: bool = False
    saw_done_sentinel: bool = False
    saw_finish_reason: bool = False
    chunks_with_wrong_model: int = 0
    errors: list[str] = field(default_factory=list)
    raw_chunks: list[dict] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"preflight HEAD /  → {self.preflight_status}  ok={self.preflight_ok}",
            f"POST /v1/chat/completions → {self.chat_status}  ok={self.chat_ok}",
            f"chunks received   = {self.chunks_received}",
            f"content chars     = {self.content_chars}",
            f"saw role chunk    = {self.saw_role_chunk}",
            f"saw finish_reason = {self.saw_finish_reason}",
            f"saw [DONE]        = {self.saw_done_sentinel}",
            f"chunks w/wrong model = {self.chunks_with_wrong_model}",
        ]
        if self.errors:
            lines.append("errors:")
            for e in self.errors:
                lines.append(f"  - {e}")
        if self.full_content:
            preview = self.full_content[:300].replace("\n", " ")
            lines.append(f"content preview: {preview}")
        return "\n".join(lines)

    @property
    def all_passed(self) -> bool:
        return (
            self.preflight_ok
            and self.chat_ok
            and self.saw_role_chunk
            and self.saw_finish_reason
            and self.saw_done_sentinel
            and self.chunks_with_wrong_model == 0
            and not self.errors
        )


class MockManyfoldAdapter:
    """One-shot adapter that drives a single chat through our endpoint and
    captures everything for assertion.

    Mirrors openclaw.adapter.ts:
      * preflight HEAD /  (5s timeout) — line :175
      * POST /v1/chat/completions with Authorization: Bearer + stream:true
        — lines :90-160
      * SSE parsing: data: {...} ... data: [DONE] — lines :200+
    """

    def __init__(self, base_url: str, token: str, timeout: float = 60.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    async def run(self, agent_id: str, prompt: str) -> AdapterResult:
        r = AdapterResult()

        # ---- Step 1: preflight HEAD / (openclaw.adapter.ts:175) ----
        # The real Manyfold gives this a 5s timeout. We mimic that.
        try:
            async with httpx.AsyncClient(timeout=5.0) as c:
                resp = await c.head(
                    f"{self.base_url}/",
                    headers={"Authorization": f"Bearer {self.token}"},
                )
                r.preflight_status = resp.status_code
                r.preflight_ok = 200 <= resp.status_code < 400
                if not r.preflight_ok:
                    r.errors.append(
                        f"preflight returned {resp.status_code}, "
                        "expected 2xx-3xx"
                    )
        except Exception as e:
            r.errors.append(f"preflight exception: {type(e).__name__}: {e}")
            return r

        # ---- Step 2: POST /v1/chat/completions, stream=true ----
        body = {
            "model": agent_id,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                async with c.stream(
                    "POST",
                    f"{self.base_url}/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self.token}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                    json=body,
                ) as resp:
                    r.chat_status = resp.status_code
                    if resp.status_code != 200:
                        # Capture body for diagnostics.
                        err_body = await resp.aread()
                        r.errors.append(
                            f"chat endpoint returned {resp.status_code}: "
                            f"{err_body.decode(errors='replace')[:300]}"
                        )
                        return r
                    r.chat_ok = True
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload == "[DONE]":
                            r.saw_done_sentinel = True
                            break
                        try:
                            chunk = json.loads(payload)
                        except Exception as e:  # noqa: BLE001
                            r.errors.append(f"malformed chunk: {e}")
                            continue
                        r.chunks_received += 1
                        r.raw_chunks.append(chunk)
                        # Verify model echo (Owner spec Part 4.4 contract).
                        if chunk.get("model") != agent_id:
                            r.chunks_with_wrong_model += 1
                        choices = chunk.get("choices") or []
                        for ch in choices:
                            delta = ch.get("delta") or {}
                            if delta.get("role") == "assistant":
                                r.saw_role_chunk = True
                            content = delta.get("content")
                            if isinstance(content, str) and content:
                                r.content_chars += len(content)
                                r.full_content += content
                            if ch.get("finish_reason"):
                                r.saw_finish_reason = True
        except Exception as e:  # noqa: BLE001
            r.errors.append(f"chat exception: {type(e).__name__}: {e}")

        return r


async def _cli_main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--prompt", default="hello — short test")
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()

    adapter = MockManyfoldAdapter(args.base_url, args.token, args.timeout)
    result = await adapter.run(args.agent_id, args.prompt)
    print(result.summary())
    print()
    print(f"verdict: {'✅ ALL CONTRACT CHECKS PASSED' if result.all_passed else '❌ CONTRACT VIOLATIONS'}")
    return 0 if result.all_passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli_main()))
