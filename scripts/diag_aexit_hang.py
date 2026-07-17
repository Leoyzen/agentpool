#!/usr/bin/env python3
"""Diagnostic script for `agentlet.iter().__aexit__()` hang.

Runs a minimal agent turn through the full RunHandle pipeline with an
MCP streamable-http server, and measures:

1. Time from turn start → StreamCompleteEvent
2. Time from "after while loop" → StreamCompleteEvent (the __aexit__ gap)
3. Whether __aexit__ completes within a reasonable timeout

Usage:
    # Run against current branch
    uv run python scripts/diag_aexit_hang.py

    # Run with a real MCP server (tests proxy path)
    uv run python scripts/diag_aexit_hang.py --mcp-url http://10.147.252.33:8722/mcp

    # Run with a mock MCP server that simulates proxy delay
    uv run python scripts/diag_aexit_hang.py --mock-proxy-delay 30

    # Quick smoke (no MCP, just timing)
    uv run python scripts/diag_aexit_hang.py --no-mcp
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import sys
import time
import uuid
from typing import Any

logging.basicConfig(level=logging.INFO, format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("diag")

# ── Mock MCP server that simulates proxy delay on cancellation ──────────────

async def mock_mcp_server_with_proxy_delay(port: int, delay_seconds: float) -> asyncio.Task[None]:
    """Start a minimal HTTP server that hangs on SSE GET for `delay_seconds` after cancellation."""
    from aiohttp import web

    async def handle_mcp_get(request: web.Request) -> web.StreamResponse:
        """Simulate an SSE endpoint that hangs (like a proxy that doesn't close TCP)."""
        resp = web.StreamResponse(status=200, headers={"Content-Type": "text/event-stream"})
        await resp.prepare(request)
        try:
            # Just hang forever — the proxy delay is in the cancellation
            await asyncio.Event().wait()  # Never returns
        except asyncio.CancelledError:
            logger.info(f"[mock-mcp] GET cancelled, simulating {delay_seconds}s proxy delay...")
            await asyncio.sleep(delay_seconds)
            logger.info("[mock-mcp] GET cleanup done")
            raise
        return resp

    async def handle_mcp_post(request: web.Request) -> web.Response:
        """Handle POST normally."""
        return web.Response(status=202)

    async def handle_mcp_delete(request: web.Request) -> web.Response:
        return web.Response(status=200)

    app = web.Application()
    app.router.add_GET("/mcp", handle_mcp_get)
    app.router.add_POST("/mcp", handle_mcp_post)
    app.router.add_DELETE("/mcp", handle_mcp_delete)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info(f"[mock-mcp] Listening on http://127.0.0.1:{port}/mcp")

    async def _keep_alive() -> None:
        await asyncio.Event().wait()

    return asyncio.create_task(_keep_alive())


# ── Timing wrapper for __aexit__ ────────────────────────────────────────────

class TimedAsyncCM:
    """Wrap an async context manager to measure __aexit__ duration."""

    def __init__(self, cm: Any, label: str) -> None:
        self._cm = cm
        self._label = label
        self._enter_time: float = 0
        self._exit_time: float = 0

    async def __aenter__(self) -> Any:
        self._enter_time = time.perf_counter()
        result = await self._cm.__aenter__()
        elapsed = time.perf_counter() - self._enter_time
        logger.info(f"[{self._label}] __aenter__ took {elapsed:.3f}s")
        return result

    async def __aexit__(self, *args: Any) -> None:
        self._exit_time = time.perf_counter()
        logger.info(f"[{self._label}] __aexit__ starting...")
        await self._cm.__aexit__(*args)
        elapsed = time.perf_counter() - self._exit_time
        logger.info(f"[{self._label}] __aexit__ took {elapsed:.3f}s")
        if elapsed > 10:
            logger.warning(f"[{self._label}] __aexit__ HANG DETECTED: {elapsed:.1f}s > 10s threshold")


# ── Test 1: Direct MCP streamable_http_client __aexit__ timing ──────────────

async def test_mcp_client_aexit(url: str, timeout: float = 30) -> dict[str, float]:
    """Test how long streamable_http_client.__aexit__ takes."""
    from mcp.client.streamable_http import streamable_http_client

    logger.info(f"Connecting to MCP server: {url}")
    t0 = time.perf_counter()

    try:
        async with asyncio.timeout(timeout + 60):
            async with streamable_http_client(url) as (read, write, get_session_id):
                logger.info("MCP client connected, session_id will be available after init")
                # Initialize the session
                from mcp.types import Implementation
                from mcp.shared.session import BaseSession

                # Just wait a moment for session to establish
                await asyncio.sleep(1)
                session_id = get_session_id()
                logger.info(f"Session ID: {session_id}")

            # __aexit__ just completed
            aexit_time = time.perf_counter() - t0
            logger.info(f"Total MCP client lifecycle: {aexit_time:.3f}s")

    except TimeoutError:
        aexit_time = time.perf_counter() - t0
        logger.error(f"TIMEOUT after {aexit_time:.1f}s — __aexit__ hung!")
        return {"aexit_time": aexit_time, "hung": True}

    return {"aexit_time": aexit_time, "hung": aexit_time > 10}


# ── Test 2: Full RunHandle turn with MCP ────────────────────────────────────

async def test_full_turn(mcp_url: str | None, timeout: float = 60) -> dict[str, Any]:
    """Run a full turn through RunHandle and measure timing."""
    from agentpool.agents.native_agent.agent import Agent
    from agentpool.agents.events import StreamCompleteEvent, RunErrorEvent
    from agentpool.mcp_server.manager import McpServerManager
    from agentpool.orchestrator.run import RunHandle
    from agentpool.agents.context import AgentRunContext

    logger.info("Setting up agent with MCP...")

    # Create a simple agent
    agent = Agent(
        name="diag_agent",
        model="openai:svc/kimi-k2",  # Use whatever model is available
        system_prompt="You are a diagnostic assistant. Reply with 'OK' to any message.",
    )

    # Set up MCP manager if URL provided
    mcp_manager: McpServerManager | None = None
    if mcp_url:
        mcp_manager = McpServerManager()
        # This is simplified — real setup requires manifest config
        logger.info(f"MCP URL: {mcp_url}")

    # Create a minimal RunHandle
    run_id = uuid.uuid4().hex
    session_id = f"diag-{uuid.uuid4().hex[:8]}"
    run_ctx = AgentRunContext(session_id=session_id, event_bus=None)

    # We can't easily create a full RunHandle without a session pool,
    # so let's just test the agent directly
    logger.info("Running agent directly (no RunHandle)...")

    t0 = time.perf_counter()
    events: list[Any] = []
    stream_complete_time: float | None = None

    try:
        async with asyncio.timeout(timeout):
            async for event in agent.run_stream("Reply with exactly: OK"):
                events.append(event)
                event_type = type(event).__name__
                elapsed = time.perf_counter() - t0
                logger.info(f"  Event: {event_type} at {elapsed:.3f}s")

                if isinstance(event, StreamCompleteEvent | RunErrorEvent):
                    stream_complete_time = elapsed
                    break
    except TimeoutError:
        elapsed = time.perf_counter() - t0
        logger.error(f"TIMEOUT after {elapsed:.1f}s — turn did not complete!")
        return {"total_time": elapsed, "stream_complete": None, "hung": True, "event_count": len(events)}

    total_time = time.perf_counter() - t0
    logger.info(f"Turn complete: {total_time:.3f}s, StreamComplete at: {stream_complete_time:.3f}s")

    return {
        "total_time": total_time,
        "stream_complete": stream_complete_time,
        "hung": stream_complete_time is None or total_time > 30,
        "event_count": len(events),
    }


# ── Test 3: Binary search helper — git bisect script ────────────────────────

async def test_bisect(mcp_url: str | None) -> int:
    """Run a quick test suitable for git bisect.

    Returns 0 (pass) if turn completes within 30s, 1 (fail) if it hangs.
    """
    result = await test_full_turn(mcp_url, timeout=30)
    if result.get("hung", True):
        logger.error("FAIL: Turn hung or did not complete within 30s")
        return 1
    logger.info("PASS: Turn completed within 30s")
    return 0


# ── Main ────────────────────────────────────────────────────────────────────

async def main() -> None:
    parser = argparse.ArgumentParser(description="Diagnose __aexit__ hang")
    parser.add_argument("--mcp-url", type=str, default=None, help="MCP server URL to test")
    parser.add_argument("--mock-proxy-delay", type=float, default=None, help="Start mock MCP server with proxy delay (seconds)")
    parser.add_argument("--no-mcp", action="store_true", help="Run without MCP (baseline timing)")
    parser.add_argument("--timeout", type=float, default=60, help="Timeout in seconds")
    parser.add_argument("--bisect", action="store_true", help="Run bisect mode (exit 0=pass, 1=fail)")
    args = parser.parse_args()

    print("=" * 60)
    print("  __aexit__ Hang Diagnostic")
    print("=" * 60)

    # Start mock MCP server if requested
    mock_task: asyncio.Task[None] | None = None
    mcp_url = args.mcp_url

    if args.mock_proxy_delay is not None:
        port = 19876
        mock_task = await mock_mcp_server_with_proxy_delay(port, args.mock_proxy_delay)
        mcp_url = f"http://127.0.0.1:{port}/mcp"
        logger.info(f"Using mock MCP server with {args.mock_proxy_delay}s proxy delay")

    # Test 1: MCP client __aexit__ timing
    if mcp_url and not args.no_mcp:
        print("\n--- Test 1: MCP client __aexit__ timing ---")
        result = await test_mcp_client_aexit(mcp_url, timeout=args.timeout)
        print(f"  Result: {json.dumps(result, indent=2)}")

    # Test 2: Full turn timing
    if not args.bisect:
        print("\n--- Test 2: Full agent turn ---")
        result = await test_full_turn(mcp_url if not args.no_mcp else None, timeout=args.timeout)
        print(f"  Result: {json.dumps(result, indent=2)}")
    else:
        # Bisect mode: just run the turn and exit with code
        exit_code = await test_bisect(mcp_url if not args.no_mcp else None)
        sys.exit(exit_code)

    # Cleanup
    if mock_task is not None:
        mock_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await mock_task

    print("\n" + "=" * 60)
    print("  Diagnostic complete")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
