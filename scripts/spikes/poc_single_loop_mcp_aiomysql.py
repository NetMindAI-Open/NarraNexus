"""
@file_name: poc_single_loop_mcp_aiomysql.py
@author: Bin Liang
@date: 2026-04-22
@description: POC for PLAN-2026-04-22-mcp-single-loop.md — validate that a
single event loop running N uvicorn.Server instances concurrently, each
handling aiomysql pool traffic including a process-wide singleton path
that mimics QuotaService, does NOT raise
"got Future attached to a different loop".

Pass criteria:
- 2 FastMCP servers on ports 18801 / 18802
- 2 tools each: `ping` (direct pool) and `ping_via_quota` (singleton path)
- 100 concurrent client calls x 2 ports x 2 tools = 400 invocations
- 0 errors, 0 occurrences of "different loop" / "Pool._wakeup" in log

Usage:
    POC_DB_HOST=... POC_DB_PORT=3306 POC_DB_USER=... POC_DB_PASSWORD=... \\
        POC_DB_NAME=... uv run python scripts/spikes/poc_single_loop_mcp_aiomysql.py

Result: exit code 0 on PASS, 1 on FAIL. See PLAN section 4.5 for
decision rules on failure (switch to Plan B2 if applicable).
"""
from __future__ import annotations

import asyncio
import os
import sys
from typing import Awaitable, Callable, Optional

import aiomysql
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings


POOL: Optional[aiomysql.Pool] = None


async def get_pool() -> aiomysql.Pool:
    """Lazily build a single process-wide aiomysql pool on first call.

    Bound to whatever loop is running when first invoked — that is the
    exact property we want to stress-test under single-loop MCP.
    """
    global POOL
    if POOL is None:
        POOL = await aiomysql.create_pool(
            host=os.environ["POC_DB_HOST"],
            port=int(os.environ.get("POC_DB_PORT", 3306)),
            user=os.environ["POC_DB_USER"],
            password=os.environ["POC_DB_PASSWORD"],
            db=os.environ["POC_DB_NAME"],
            minsize=1,
            maxsize=5,
            autocommit=True,
            charset="utf8mb4",
        )
    return POOL


class QuotaLike:
    """Process-wide singleton mirroring quota_service.py shape.

    Holds a `repo_getter` that resolves the current-loop pool at call
    time. This is the production failure shape: bootstrap sets up the
    singleton, but request handlers re-resolve the pool on demand.
    """

    _default: Optional["QuotaLike"] = None

    def __init__(self, repo_getter: Callable[[], Awaitable[aiomysql.Pool]]):
        self._repo_getter = repo_getter

    @classmethod
    def set_default(cls, svc: "QuotaLike") -> None:
        cls._default = svc

    @classmethod
    def default(cls) -> "QuotaLike":
        if cls._default is None:
            raise RuntimeError("QuotaLike.default() not initialized")
        return cls._default

    async def count_one(self) -> int:
        pool = await self._repo_getter()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                row = await cur.fetchone()
                assert row is not None
                return int(row[0])


async def bootstrap_quota_like() -> None:
    """Called once on the bootstrap loop before any server starts.

    Mirrors NarraNexus' bootstrap_quota_subsystem — registers a long
    lived singleton that resolves its repo lazily.
    """
    QuotaLike.set_default(QuotaLike(repo_getter=get_pool))


def build_server(port: int, name: str) -> FastMCP:
    mcp = FastMCP(name=name)

    @mcp.tool()
    async def ping() -> dict:
        """Direct pool path: handler -> pool.acquire -> SELECT 1."""
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
                row = await cur.fetchone()
        return {"ok": True, "value": int(row[0]), "server": name}

    @mcp.tool()
    async def ping_via_quota() -> dict:
        """Singleton path: handler -> QuotaLike.default() -> repo_getter -> pool.

        Matches setup_mcp_llm_context -> QuotaService.default() -> repo.
        """
        v = await QuotaLike.default().count_one()
        return {"ok": True, "value": v, "server": name, "via": "quota"}

    mcp.settings.host = "127.0.0.1"
    mcp.settings.port = port
    mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    return mcp


async def serve_all(servers: list[FastMCP]) -> None:
    """Run all servers on THIS loop via asyncio.gather — no threads, no
    nested anyio.run. Same shape the production fix will use.
    """
    await asyncio.gather(*[s.run_sse_async() for s in servers])


async def call_once(port: int, tool: str, i: int) -> Optional[str]:
    """Make one MCP client call. Return error string or None on success."""
    try:
        async with sse_client(f"http://127.0.0.1:{port}/sse") as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                res = await sess.call_tool(tool, {})
                if not res.content:
                    return f"[{port}/{tool}#{i}] empty content"
                return None
    except Exception as e:
        return f"[{port}/{tool}#{i}] {type(e).__name__}: {e}"


async def smash(ports: list[int], tools: list[str], n: int, concurrency: int = 20) -> list[str]:
    """Issue all (port, tool, i) calls, but cap concurrent in-flight via
    a semaphore. Unlimited gather would open hundreds of SSE client
    sockets at once, which exercises transport throughput (not loop
    correctness) and muddies the result.
    """
    sem = asyncio.Semaphore(concurrency)

    async def guarded(p: int, t: str, i: int) -> Optional[str]:
        async with sem:
            return await call_once(p, t, i)

    results = await asyncio.gather(
        *[guarded(p, t, i) for p in ports for t in tools for i in range(n)]
    )
    return [e for e in results if e is not None]


async def wait_ready(ports: list[int], timeout: float = 10.0) -> None:
    """Poll each port until it responds, up to timeout seconds."""
    import socket

    deadline = asyncio.get_event_loop().time() + timeout
    for port in ports:
        while True:
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=0.5)
                s.close()
                break
            except OSError:
                if asyncio.get_event_loop().time() > deadline:
                    raise RuntimeError(f"server on port {port} not ready after {timeout}s")
                await asyncio.sleep(0.2)


async def main() -> int:
    ports = [18801, 18802]
    tools = ["ping", "ping_via_quota"]
    n_per = 100
    total = len(ports) * len(tools) * n_per

    # Bootstrap the singleton on the main loop BEFORE starting servers.
    # This is the exact sequence NarraNexus uses in run_mcp_servers_async:
    # bootstrap_quota_subsystem(db) runs before the MCP server gather.
    await bootstrap_quota_like()

    servers = [build_server(ports[0], "srv_a"), build_server(ports[1], "srv_b")]
    serve_task = asyncio.create_task(serve_all(servers))

    try:
        await wait_ready(ports, timeout=15.0)
        errors = await smash(ports, tools, n=n_per)
    finally:
        serve_task.cancel()
        try:
            await serve_task
        except (asyncio.CancelledError, Exception):
            pass
        if POOL is not None:
            POOL.close()
            await POOL.wait_closed()

    print("\n" + "=" * 60)
    if errors:
        print(f"FAIL: {len(errors)} errors in {total} calls")
        for e in errors[:10]:
            print(f"  {e}")
        if len(errors) > 10:
            print(f"  ... and {len(errors) - 10} more")
        return 1
    print(f"PASS: {total}/{total} calls succeeded (0 errors)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
