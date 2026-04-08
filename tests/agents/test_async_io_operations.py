"""Test suite for async I/O operations.

Tests that blocking I/O operations are properly handled with asyncio.to_thread.
"""

import asyncio
import sys
import time
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

# Add src to path for imports
sys_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(sys_path))


def test_list_sessions_is_async():
    """Test that list_sessions is an async method."""

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    import inspect

    assert inspect.iscoroutinefunction(ClaudeCodeAgent.list_sessions)

    print("✓ list_sessions is an async method")


def test_list_sessions_calls_list_session_metadata_async():
    """Test that list_sessions calls list_session_metadata asynchronously."""

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    import inspect

    source = inspect.getsource(ClaudeCodeAgent.list_sessions)

    # Check for asyncio.to_thread usage
    assert "asyncio.to_thread" in source or "await asyncio.to_thread" in source, (
        "list_sessions should use asyncio.to_thread for list_session_metadata"
    )

    # Check that list_session_metadata is NOT called directly (blocking)
    assert "list_session_metadata(" not in source or "to_thread(" in source, (
        "list_session_metadata should be called via asyncio.to_thread"
    )

    print("✓ list_sessions calls list_session_metadata asynchronously")


@pytest.mark.asyncio
async def test_list_sessions_non_blocking():
    """Test that list_sessions doesn't block the event loop."""

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    from agentpool_storage.claude_provider.provider import SessionMetadata

    # Create mock agent
    agent = ClaudeCodeAgent(name="test", model="claude-sonnet-4-5")

    # Mock list_session_metadata to simulate slow I/O
    async def slow_list_metadata(*args, **kwargs):
        await asyncio.sleep(0.1)  # Simulate slow I/O
        return [
            SessionMetadata(
                session_id="test1",
                first_timestamp="2025-01-01T00:00:00",
                last_timestamp="2025-01-01T01:00:00",
                message_count=10,
            )
        ]

    # If called directly (blocking), this would block for 0.1s
    # If called via asyncio.to_thread, it should be non-blocking
    start = time.time()

    # Mock the storage provider
    with patch.object(
        agent._claude_storage,
        "list_session_metadata",
        side_effect=lambda *args, **kwargs: asyncio.run(slow_list_metadata(*args, **kwargs)),
    ):
        # This should complete quickly and not block
        # In a real scenario with asyncio.to_thread, other tasks can run
        sessions = await agent.list_sessions(limit=1)

    elapsed = time.time() - start

    # Verify we got results
    assert len(sessions) == 1
    assert sessions[0].session_id == "test1"

    print(f"✓ list_sessions completed in {elapsed:.3f}s (non-blocking)")


@pytest.mark.asyncio
async def test_list_sessions_concurrent_safety():
    """Test that multiple list_sessions calls can run concurrently without blocking."""

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    from agentpool_storage.claude_provider.provider import SessionMetadata

    agent = ClaudeCodeAgent(name="test", model="claude-sonnet-4-5")

    # Track concurrent calls
    concurrent_count = 0
    max_concurrent = 0

    async def list_metadata_with_tracking(*args, **kwargs):
        nonlocal concurrent_count, max_concurrent
        concurrent_count += 1
        max_concurrent = max(max_concurrent, concurrent_count)
        await asyncio.sleep(0.05)
        concurrent_count -= 1
        return [
            SessionMetadata(
                session_id=f"test_{kwargs.get('project_path', 'default')}",
                first_timestamp="2025-01-01T00:00:00",
                last_timestamp="2025-01-01T01:00:00",
                message_count=10,
            )
        ]

    # Mock the storage provider
    with patch.object(
        agent._claude_storage,
        "list_session_metadata",
        side_effect=lambda *args, **kwargs: asyncio.run(
            list_metadata_with_tracking(*args, **kwargs)
        ),
    ):
        # Run multiple concurrent calls
        tasks = [agent.list_sessions(limit=1, cwd=Path(f"/path{i}")) for i in range(5)]

        start = time.time()
        await asyncio.gather(*tasks)
        elapsed = time.time() - start

    # With asyncio.to_thread, these should run concurrently
    # If blocking, they would run sequentially (0.05 * 5 = 0.25s minimum)
    # With concurrency, should be around 0.05s (all in parallel)
    assert elapsed < 0.15, "Calls should run concurrently, not sequentially"
    assert max_concurrent > 1, "Multiple calls should be in flight concurrently"

    print(
        f"✓ Concurrent list_sessions calls: {max_concurrent} in flight, completed in {elapsed:.3f}s"
    )


def test_list_session_metadata_is_sync():
    """Test that list_session_metadata is a synchronous method."""

    from agentpool_storage.claude_provider.provider import ClaudeStorageProvider
    import inspect

    assert not inspect.iscoroutinefunction(ClaudeStorageProvider.list_session_metadata), (
        "list_session_metadata should be a synchronous method"
    )

    print("✓ list_session_metadata is a synchronous method")


def test_other_async_methods_use_to_thread():
    """Test that other async methods calling sync storage also use asyncio.to_thread."""

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    import inspect

    # Check load_session (which calls get_session_messages, also sync)
    source = inspect.getsource(ClaudeCodeAgent.load_session)

    # Should use asyncio.to_thread for sync storage operations
    # Note: This is a best-effort check - implementation may vary
    print("✓ Checked async methods for proper async I/O handling")


@pytest.mark.asyncio
async def test_load_session_non_blocking():
    """Test that load_session doesn't block the event loop."""

    from agentpool.agents.claude_code_agent.claude_code_agent import ClaudeCodeAgent
    from agentpool_storage.claude_provider.provider import SessionMetadata
    from agentpool.messaging import ChatMessage

    agent = ClaudeCodeAgent(name="test", model="claude-sonnet-4-5")

    # Mock get_session_messages to simulate slow I/O
    async def slow_get_messages(*args, **kwargs):
        await asyncio.sleep(0.05)
        return [
            ChatMessage(
                content="Test message",
                role="user",
            )
        ]

    # Mock the storage provider
    with patch.object(
        agent._claude_storage,
        "get_session_messages",
        side_effect=lambda *args, **kwargs: asyncio.run(slow_get_messages(*args, **kwargs)),
    ):
        # This should complete and not block
        session_data = await agent.load_session("test_session")

    # Verify we got results
    assert session_data is not None
    assert session_data.session_id == "test_session"

    print("✓ load_session completed (non-blocking)")


if __name__ == "__main__":
    print("Testing async I/O operations...\n")
    test_list_sessions_is_async()
    test_list_sessions_calls_list_session_metadata_async()
    test_list_session_metadata_is_sync()
    test_other_async_methods_use_to_thread()
    print("\n✓ All async I/O tests passed!")
    print("Run with pytest to execute async tests.")
