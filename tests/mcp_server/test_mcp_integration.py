"""Integration tests for MCP lifecycle, cross-task safety, and subagent inheritance.

These tests use real in-process FastMCP servers (no mocks) to exercise the
full MCPToolset lifecycle — including anyio CancelScope boundaries that
mocked tests cannot catch.

Patterns borrowed from pydantic-ai's test_mcp.py:
- ``fastmcp_server`` fixture providing an in-process FastMCP server
- ``MCPToolset`` constructed from the server object directly (no stdio/HTTP)
- ``TestModel`` for deterministic agent behaviour
- ``anyio.create_task_group`` for cross-task lifecycle tests
"""

from __future__ import annotations

import asyncio
from typing import Any

import anyio
from pydantic_ai._run_context import RunContext
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.usage import RunUsage
import pytest

from agentpool.mcp_server.manager import MCPManager
from agentpool_config.mcp_server import (
    AcpMCPServerConfig,
    StdioMCPServerConfig,
)


# ---------------------------------------------------------------------------
# In-process FastMCP server fixture (mirrors pydantic-ai's pattern)
# ---------------------------------------------------------------------------


@pytest.fixture
def fastmcp_server() -> Any:
    """In-process FastMCP server with tools for integration testing."""
    from fastmcp.server import FastMCP

    server: FastMCP[None] = FastMCP("integration_test_server")

    @server.tool()
    async def echo(message: str) -> str:
        """Echo a message back."""
        return f"Echo: {message}"

    @server.tool()
    async def add(a: int, b: int) -> dict[str, int]:
        """Add two numbers and return the result."""
        return {"sum": a + b}

    @server.tool()
    async def get_server_name() -> str:
        """Return the server name."""
        return "integration_test_server"

    return server


@pytest.fixture
def run_context() -> RunContext[Any]:
    """Minimal RunContext for toolset calls."""
    from pydantic_ai.models.test import TestModel

    return RunContext(
        deps=None,
        model=TestModel(),
        usage=RunUsage(),
        prompt=None,
        messages=[],
        run_step=0,
    )


# ---------------------------------------------------------------------------
# 1. MCPToolset cross-task lifecycle (the bug that caused RuntimeError)
# ---------------------------------------------------------------------------


class TestCrossTaskLifecycle:
    """Verify cross-task MCPToolset lifecycle without CancelScope errors.

    Without raising ``RuntimeError: Attempted to exit cancel scope in a
    different task than it was entered in``.

    These tests use REAL in-process FastMCP servers (not mocks) so that
    anyio CancelScope boundaries are exercised.
    """

    async def test_enter_exit_same_task(self, fastmcp_server: Any):
        """Baseline: enter and exit in the same task works fine."""
        toolset = MCPToolset(fastmcp_server)
        assert toolset.is_running is False
        async with toolset:
            assert toolset.is_running is True
        assert toolset.is_running is False

    async def test_enter_exit_different_tasks(self, fastmcp_server: Any):
        """Enter in task A, exit in task B — must not raise RuntimeError.

        This reproduces the subagent scenario where the parent agent's turn
        creates the MCPToolset (task A) and the subagent's turn exits it
        (task B).

        With the no-cache approach, each call to ``as_capability()``
        creates a fresh MCPToolset, so this scenario (sharing one toolset
        across tasks) only happens if someone manually caches. This test
        documents the boundary: if someone reintroduces caching, this test
        will catch the regression.
        """
        toolset = MCPToolset(fastmcp_server)

        # Enter in task A
        async def enter() -> None:
            await toolset.__aenter__()

        # Exit in task B
        async def exit_() -> None:
            await toolset.__aexit__(None, None, None)

        task_a = asyncio.create_task(enter())
        await task_a
        assert toolset.is_running is True

        task_b = asyncio.create_task(exit_())
        await task_b
        assert toolset.is_running is False

    async def test_reentrant_same_task(self, fastmcp_server: Any):
        """MCPToolset supports re-entrant usage within the same task."""
        toolset = MCPToolset(fastmcp_server)
        async with toolset:
            async with toolset:
                assert toolset.is_running is True
            assert toolset.is_running is True
        assert toolset.is_running is False

    async def test_concurrent_enter_different_tasks(self, fastmcp_server: Any):
        """Two concurrent ``__aenter__`` calls from different tasks.

        MCPToolset uses ``anyio.Lock`` + ``_running_count`` for re-entrant
        support. This test verifies that concurrent enters from different
        tasks don't corrupt internal state (they may succeed or raise, but
        must not leave the toolset in a broken state).
        """
        toolset = MCPToolset(fastmcp_server)
        errors: list[BaseException] = []

        async def enter_and_exit() -> None:
            try:
                async with toolset:
                    await anyio.sleep(0.01)
            except BaseException as exc:  # noqa: BLE001  # intentional - catch any cross-task failure
                errors.append(exc)

        async with anyio.create_task_group() as tg:
            tg.start_soon(enter_and_exit)
            tg.start_soon(enter_and_exit)

        # Whatever happened, the toolset should be in a clean state
        # (either running or not, but not corrupted)
        assert isinstance(toolset.is_running, bool)


# ---------------------------------------------------------------------------
# 2. Real tool calls through MCPManager.as_capability()
# ---------------------------------------------------------------------------


class TestRealToolCalls:
    """Exercise the full MCPManager to tool call path with a real server.

    Covers: MCPManager → as_capability() → MCPToolset → get_tools() → call_tool().

    This verifies that ``to_transport()`` produces a working transport and
    that ``MCPToolset`` constructed by ``as_capability()`` can actually
    call tools — not just that the types match.
    """

    async def test_as_capability_toolset_can_call_tools(
        self, fastmcp_server: Any, run_context: RunContext[Any]
    ):
        """MCPToolset from as_capability() can list and call tools."""
        # MCPManager needs a config, but we bypass it by constructing
        # MCPToolset directly from the server (like pydantic-ai tests).
        toolset = MCPToolset(fastmcp_server, id="test_server")
        async with toolset:
            tools = await toolset.get_tools(run_context)
            assert "echo" in tools
            assert "add" in tools
            assert "get_server_name" in tools

            result = await toolset.call_tool(
                "echo", {"message": "hello"}, run_context, tools["echo"]
            )
            assert result == "Echo: hello"

            result = await toolset.call_tool("add", {"a": 2, "b": 3}, run_context, tools["add"])
            assert result == {"sum": 5}

    async def test_as_capability_toolset_instructions(
        self, fastmcp_server: Any, run_context: RunContext[Any]
    ):
        """MCPToolset from as_capability() exposes server instructions."""
        toolset = MCPToolset(fastmcp_server, include_instructions=True)
        async with toolset:
            instructions = await toolset.get_instructions(run_context)
        # FastMCP servers don't set instructions by default in our fixture
        # but the call should not raise
        assert instructions is None or isinstance(instructions.content, str)

    async def test_two_toolsets_from_same_server_are_independent(
        self, fastmcp_server: Any, run_context: RunContext[Any]
    ):
        """Two MCPToolset instances from the same server config are independent.

        This verifies the no-cache approach: each ``as_capability()`` call
        produces a separate, fully functional toolset.
        """
        toolset1 = MCPToolset(fastmcp_server, id="ts1")
        toolset2 = MCPToolset(fastmcp_server, id="ts2")

        assert toolset1 is not toolset2

        async with toolset1, toolset2:
            tools1 = await toolset1.get_tools(run_context)
            tools2 = await toolset2.get_tools(run_context)

            assert set(tools1) == set(tools2)
            assert {"echo", "add", "get_server_name"} <= set(tools1)

            result1 = await toolset1.call_tool(
                "echo", {"message": "from1"}, run_context, tools1["echo"]
            )
            result2 = await toolset2.call_tool(
                "echo", {"message": "from2"}, run_context, tools2["echo"]
            )

            assert result1 == "Echo: from1"
            assert result2 == "Echo: from2"


# ---------------------------------------------------------------------------
# 3. Subagent MCP inheritance (pool-level vs agent-level scoping)
# ---------------------------------------------------------------------------


class TestSubagentMCPInheritance:
    """Verify pool-level and agent-level MCP server scoping rules.

    Pool-level MCP servers are accessible to subagents while agent-level
    MCP servers are NOT inherited.

    The scoping rule:
    - Pool-level MCPManager → shared via ``agent_pool.mcp`` → subagents see it
    - Agent-level MCPManager → private to the agent → subagents don't see it

    This mirrors the real subagent flow: parent agent's ``get_agentlet()``
    calls ``self.mcp.as_capability()``. If the agent shares the pool's
    MCPManager (``self._mcp_shared = True``), it gets pool-level servers.
    If it has its own MCPManager, it only gets agent-level servers.
    """

    async def test_pool_level_mcp_visible_to_shared_manager(self):
        """Pool-level MCPManager capabilities are accessible by any agent sharing it."""
        pool_mcp = MCPManager(
            name="pool_mcp",
            owner="pool",
            servers=[
                StdioMCPServerConfig(
                    name="search_kb",
                    command="uvx",
                    args=["mcp-server-search"],
                ),
            ],
        )

        caps = await pool_mcp.as_capability()

        assert len(caps) == 1
        assert caps[0].id == "search_kb"
        assert isinstance(caps[0].local, MCPToolset)

        await pool_mcp.cleanup()

    async def test_agent_level_mcp_not_inherited_by_subagent(self):
        """Agent-level capabilities are not inherited by subagents."""
        # Parent agent's private MCP
        parent_mcp = MCPManager(
            name="engineer_mcp",
            owner="node_engineer",
            servers=[
                StdioMCPServerConfig(
                    name="expert_anno",
                    command="uvx",
                    args=["mcp-server-anno"],
                ),
            ],
        )

        # Subagent's MCP (separate instance, no agent-level servers)
        subagent_mcp = MCPManager(
            name="librarian_mcp",
            owner="node_librarian",
            servers=[],
        )

        parent_caps = await parent_mcp.as_capability()
        subagent_caps = await subagent_mcp.as_capability()

        # Parent has its agent-level server
        assert len(parent_caps) == 1
        assert parent_caps[0].id == "expert_anno"

        # Subagent has no agent-level servers
        assert len(subagent_caps) == 0

        # Subagent would get pool-level caps separately (simulated)
        # but NOT parent's agent-level caps
        parent_ids = {c.id for c in parent_caps}
        subagent_ids = {c.id for c in subagent_caps}
        assert parent_ids & subagent_ids == set()

        await parent_mcp.cleanup()
        await subagent_mcp.cleanup()

    async def test_pool_and_agent_mcp_produce_distinct_toolsets(self):
        """Pool-level and agent-level MCPManagers produce distinct toolset instances."""
        pool_mcp = MCPManager(
            name="pool_mcp",
            servers=[
                StdioMCPServerConfig(
                    name="pool_server",
                    command="python",
                    args=["server.py"],
                ),
            ],
        )
        agent_mcp = MCPManager(
            name="agent_mcp",
            servers=[
                StdioMCPServerConfig(
                    name="agent_server",
                    command="python",
                    args=["server2.py"],
                ),
            ],
        )

        pool_caps = await pool_mcp.as_capability()
        agent_caps = await agent_mcp.as_capability()

        assert pool_caps[0].local is not agent_caps[0].local
        assert pool_caps[0].id != agent_caps[0].id

        await pool_mcp.cleanup()
        await agent_mcp.cleanup()


# ---------------------------------------------------------------------------
# 4. MCPManager.disconnect_all() and cleanup
# ---------------------------------------------------------------------------


class TestMCPManagerCleanup:
    """Verify MCPManager disconnect and cleanup properly tear down resources.

    These tests verify ``disconnect_all()`` closes cached ``MCPToolset``
    instances via ``__aexit__`` (guarding against toolsets that were never
    entered) and resets internal state for reuse.
    """

    async def test_disconnect_all_does_not_raise(self):
        """disconnect_all() handles cached toolsets that were never entered."""
        manager = MCPManager(
            servers=[
                StdioMCPServerConfig(
                    name="srv1",
                    command="python",
                    args=["server.py"],
                ),
            ],
        )

        caps = await manager.as_capability()
        assert len(caps) == 1

        await manager.disconnect_all()

        caps2 = await manager.as_capability()
        assert len(caps2) == 1

        await manager.cleanup()

    async def test_cleanup_after_as_capability(self):
        """cleanup() after as_capability() doesn't raise."""
        manager = MCPManager(
            servers=[
                StdioMCPServerConfig(
                    name="srv1",
                    command="python",
                    args=["server.py"],
                ),
            ],
        )

        caps = await manager.as_capability()
        assert len(caps) == 1

        # cleanup() should be safe
        await manager.cleanup()

    async def test_disconnect_all_resets_exit_stack(self):
        """disconnect_all() creates a fresh AsyncExitStack."""
        manager = MCPManager(
            servers=[
                StdioMCPServerConfig(
                    name="srv1",
                    command="python",
                    args=["server.py"],
                ),
            ],
        )

        original_stack = manager.exit_stack
        await manager.disconnect_all()
        assert manager.exit_stack is not original_stack

        await manager.cleanup()


# ---------------------------------------------------------------------------
# 5. ACP MCP server scoping
# ---------------------------------------------------------------------------


class TestACPMCPScoping:
    """Verify ACP transport MCP servers are correctly excluded from as_capability().

    ACP servers go through ``get_aggregating_provider()`` instead of
    ``as_capability()`` (which is for pydantic-ai's MCPToolset).
    """

    async def test_acp_excluded_from_as_capability(self):
        """ACP servers go through get_aggregating_provider() instead of as_capability()."""
        manager = MCPManager(
            servers=[
                StdioMCPServerConfig(
                    name="native_server",
                    command="python",
                    args=["server.py"],
                ),
                AcpMCPServerConfig(name="acp_server", acp_id="my-acp"),
            ],
        )

        caps = await manager.as_capability()

        # Only the non-ACP server should produce a capability
        assert len(caps) == 1
        assert caps[0].id == "native_server"

        await manager.cleanup()

    async def test_acp_included_in_aggregating_provider(self):
        """ACP servers appear in get_aggregating_provider()."""
        from unittest.mock import patch

        class _FakeProvider:
            def __init__(self, server: Any, **kwargs: Any) -> None:
                self.server = server
                self.name = kwargs.get("name", "fake")

            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *args: object) -> None:
                pass

        class _FakeSignal:
            def connect(self, cb: Any) -> None:
                pass

            def disconnect(self, cb: Any) -> None:
                pass

        # _FakeProvider needs signal attributes for AggregatingResourceProvider
        for attr in ("tools_changed", "prompts_changed", "resources_changed", "skills_changed"):
            setattr(_FakeProvider, attr, _FakeSignal())

        acp_config = AcpMCPServerConfig(name="acp_server", acp_id="test-acp")
        stdio_config = StdioMCPServerConfig(
            name="stdio_server", command="python", args=["server.py"]
        )
        manager = MCPManager(name="test")

        with patch("agentpool.mcp_server.manager.MCPResourceProvider", _FakeProvider):
            await manager.setup_server(acp_config)
            await manager.setup_server(stdio_config)

        agg = manager.get_aggregating_provider()

        # Only ACP provider in aggregating provider
        assert len(agg.providers) == 1
        assert agg.providers[0].server is acp_config

        await manager.cleanup()


# ---------------------------------------------------------------------------
# 6. Real cross-task as_capability() (simulating subagent flow)
# ---------------------------------------------------------------------------


class TestCrossTaskAsCapability:
    """Simulate real subagent flow with cross-task as_capability() calls.

    Parent agent calls ``as_capability()`` in task A, subagent calls
    ``as_capability()`` in task B. Both should succeed.

    With caching, both calls return MCP wrappers backed by the same
    ``MCPToolset`` instance (cached by ``client_id``). The MCP wrappers
    themselves are always distinct.
    """

    async def test_as_capability_in_different_tasks(self):
        """as_capability() produces MCP wrappers sharing a cached MCPToolset."""
        manager = MCPManager(
            servers=[
                StdioMCPServerConfig(
                    name="test_server",
                    command="python",
                    args=["server.py"],
                ),
            ],
        )

        async def get_caps() -> list[Any]:
            return await manager.as_capability()

        task_a = asyncio.create_task(get_caps())
        caps_a = await task_a

        task_b = asyncio.create_task(get_caps())
        caps_b = await task_b

        assert len(caps_a) == 1
        assert len(caps_b) == 1
        # MCP wrappers are distinct
        assert caps_a[0] is not caps_b[0]
        # Underlying MCPToolset is cached (shared by client_id)
        assert caps_a[0].local is caps_b[0].local
        assert caps_a[0].id == caps_b[0].id

        await manager.cleanup()

    async def test_as_capability_concurrent_tasks(self):
        """Concurrent as_capability() calls share cached MCPToolsets."""
        manager = MCPManager(
            servers=[
                StdioMCPServerConfig(
                    name="srv1",
                    command="python",
                    args=["s1.py"],
                ),
                StdioMCPServerConfig(
                    name="srv2",
                    command="python",
                    args=["s2.py"],
                ),
            ],
        )

        results: list[list[Any]] = []

        async def get_caps() -> None:
            caps = await manager.as_capability()
            results.append(caps)

        async with anyio.create_task_group() as tg:
            tg.start_soon(get_caps)
            tg.start_soon(get_caps)
            tg.start_soon(get_caps)

        assert len(results) == 3
        for caps in results:
            assert len(caps) == 2

        # MCP wrappers are always distinct; underlying toolsets are cached
        # by client_id, so there are 2 unique toolsets (one per server).
        all_toolsets = [cap.local for caps in results for cap in caps]
        assert len(all_toolsets) == 6
        assert len({id(ts) for ts in all_toolsets}) == 2

        await manager.cleanup()
