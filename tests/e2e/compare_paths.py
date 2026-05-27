"""Side-by-side comparison: same agent / same user / two paths.

Path A — native UI WebSocket: /ws/agent/run?x_user_id=bin
         working_source = chat (default)
Path B — manyfold OpenAI    : POST /v1/chat/completions  Bearer <gateway>
         working_source = manyfold

Both call the same NarraNexus container. Whatever diverges is the smoking gun
for "why does CHAT succeed but MANYFOLD fail with invalid beta flag".
"""

from __future__ import annotations
import asyncio, json, os, sys, time
import httpx
import websockets


HOST = os.environ.get("NX_HOST", "127.0.0.1:18000")
TOKEN = os.environ.get("MANYFOLD_TOKEN") or open("/tmp/nx-manyfold-demo-token").read().strip()
AGENT = os.environ.get("AGENT_ID", "demo_agent_001")
USER = os.environ.get("USER_ID", "bin")
PROMPT = os.environ.get("PROMPT", "Reply with exactly: hi")


async def path_a_ws():
    print(f"=== Path A: WS /ws/agent/run x_user_id={USER} (working_source=chat) ===")
    url = f"ws://{HOST}/ws/agent/run?x_user_id={USER}"
    try:
        async with websockets.connect(url, open_timeout=10) as ws:
            await ws.send(json.dumps({
                "agent_id": AGENT,
                "user_id": USER,
                "input_content": PROMPT,
            }))
            # Receive for up to 90s
            t0 = time.time()
            seen_types = []
            agent_response_chars = 0
            error_msg = None
            while time.time() - t0 < 90:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=20)
                except asyncio.TimeoutError:
                    print("  recv timed out")
                    break
                ev = json.loads(raw)
                t = ev.get("type", "?")
                seen_types.append(t)
                if t == "agent_response":
                    agent_response_chars += len(ev.get("delta", "") or "")
                if t in ("run_ended", "completed", "done", "failed", "cancelled"):
                    print(f"  terminal type={t}")
                    break
                if t == "error":
                    error_msg = ev.get("error_message") or ev.get("message") or ev.get("error")
                    print(f"  error: {error_msg}")
                    break
            print(f"  events seen ({len(seen_types)}): {seen_types[:30]}{'...' if len(seen_types)>30 else ''}")
            print(f"  agent_response total chars: {agent_response_chars}")
            print(f"  error: {error_msg}")
    except Exception as e:
        print(f"  PATH A FAILED: {type(e).__name__}: {e}")


async def path_b_manyfold():
    print(f"=== Path B: POST /v1/chat/completions Bearer (working_source=manyfold) ===")
    url = f"http://{HOST}/v1/chat/completions"
    body = {
        "model": AGENT,
        "messages": [{"role": "user", "content": PROMPT}],
        "stream": True,
    }
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    chunks = 0
    content_chars = 0
    async with httpx.AsyncClient(timeout=120.0) as c:
        async with c.stream("POST", url, headers=headers, json=body) as resp:
            print(f"  status {resp.status_code}")
            if resp.status_code != 200:
                body_bytes = await resp.aread()
                print(f"  body: {body_bytes.decode(errors='replace')[:300]}")
                return
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    print("  saw [DONE]")
                    break
                try:
                    ch = json.loads(payload)
                except Exception:
                    continue
                chunks += 1
                content = (ch.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                if content:
                    content_chars += len(content)
                if chunks <= 5:
                    print(f"  chunk #{chunks}: {json.dumps(ch)[:160]}")
            print(f"  total chunks: {chunks}, content chars: {content_chars}")


async def main():
    print(f"target: http://{HOST}  agent={AGENT}  user={USER}")
    print(f"prompt: {PROMPT!r}\n")
    # B first because it's the failure case; saves time on stuck WS
    await path_b_manyfold()
    print()
    await path_a_ws()


if __name__ == "__main__":
    asyncio.run(main())
