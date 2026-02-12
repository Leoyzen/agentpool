"""Tests for session hierarchy functionality."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.sessions import SessionManager, SessionData
from agentpool.sessions.store import MemorySessionStore
from agentpool_storage.session_store import SQLSessionStore
from agentpool_config.storage import SQLStorageConfig


@pytest.fixture
def mock_pool():
    """Create a mock pool with all_agents attribute."""
    pool = MagicMock()
    pool.all_agents = [
        "coordinator",
        "coder",
        "root_agent",
        "parent_agent",
        "child_agent",
        "child_agent2",
        "agent",
        "other_agent",
    ]
    return pool


@pytest.fixture
def memory_store():
    """Create a memory session store for testing."""
    return MemorySessionStore()


@pytest.fixture
def sql_store(tmp_path):
    """Create a SQL session store with temp database."""
    db_path = tmp_path / "test_hierarchy.db"
    config = SQLStorageConfig(url=f"sqlite:///{db_path}")
    return SQLSessionStore(config)


class TestSessionHierarchy:
    """Tests for session parent-child hierarchy."""

    async def test_create_with_parent_id(self, mock_pool) -> None:
        """Test that parent_id is persisted correctly."""
        manager = SessionManager(mock_pool)

        async with manager:
            # Create parent session
            parent = await manager.create(agent_name="coordinator")

            # Create child session
            child = await manager.create(
                agent_name="coder",
                parent_id=parent.session_id,
            )

            # Verify child has parent_id
            assert child.parent_id == parent.session_id

            # Verify when loaded
            loaded = await manager.get(child.session_id)
            assert loaded.parent_id == parent.session_id

    async def test_list_by_parent_id_memory(self, mock_pool) -> None:
        """Test filtering sessions by parent_id with memory store."""
        manager = SessionManager(mock_pool)

        async with manager:
            # Create sessions
            root = await manager.create(agent_name="root_agent")
            parent = await manager.create(agent_name="parent_agent")
            child1 = await manager.create(agent_name="child_agent", parent_id=parent.session_id)
            child2 = await manager.create(agent_name="child_agent2", parent_id=parent.session_id)

            # List children of parent
            children = await manager.list_sessions(parent_id=parent.session_id)

            # Verify only children of parent are returned
            assert len(children) == 2
            assert child1.session_id in children
            assert child2.session_id in children
            assert root.session_id not in children
            assert parent.session_id not in children

    async def test_list_by_parent_id_sql(self, mock_pool, sql_store) -> None:
        """Test filtering sessions by parent_id with SQL store."""
        manager = SessionManager(mock_pool, store=sql_store)

        async with manager:
            # Create sessions
            root = await manager.create(agent_name="root_agent")
            parent = await manager.create(agent_name="parent_agent")
            child1 = await manager.create(agent_name="child_agent", parent_id=parent.session_id)
            child2 = await manager.create(agent_name="child_agent2", parent_id=parent.session_id)

            # List children of parent
            children = await manager.list_sessions(parent_id=parent.session_id)

            # Verify only children of parent are returned
            assert len(children) == 2
            assert child1.session_id in children
            assert child2.session_id in children
            assert root.session_id not in children
            assert parent.session_id not in children

    async def test_create_with_invalid_parent(self, mock_pool) -> None:
        """Test that creating with non-existent parent_id succeeds (permissive)."""
        manager = SessionManager(mock_pool)

        async with manager:
            # Create child with fake parent_id
            session = await manager.create(
                agent_name="agent",
                parent_id="nonexistent_parent_id",
            )

            # Should succeed (permissive validation)
            assert session.parent_id == "nonexistent_parent_id"

            # Verify persisted correctly
            loaded = await manager.get(session.session_id)
            assert loaded.parent_id == "nonexistent_parent_id"

    async def test_list_by_parent_id_with_no_children(self, mock_pool) -> None:
        """Test filtering by parent_id returns empty list when no children exist."""
        manager = SessionManager(mock_pool)

        async with manager:
            # Create parent but no children
            await manager.create(agent_name="root_agent", session_id="parent_1")
            await manager.create(agent_name="other_agent")

            # List children of non-existent parent
            children = await manager.list_sessions(parent_id="nonexistent_parent")

            # Should return empty list
            assert len(children) == 0

    async def test_nested_hierarchy(self, mock_pool) -> None:
        """Test multi-level hierarchy (grandparent -> parent -> child)."""
        manager = SessionManager(mock_pool)

        async with manager:
            # Create three levels
            grandparent = await manager.create(agent_name="root_agent")
            parent = await manager.create(
                agent_name="parent_agent", parent_id=grandparent.session_id
            )
            child = await manager.create(agent_name="child_agent", parent_id=parent.session_id)

            # Verify hierarchy through list operations
            grandparent_children = await manager.list_sessions(parent_id=grandparent.session_id)
            assert len(grandparent_children) == 1
            assert parent.session_id in grandparent_children

            parent_children = await manager.list_sessions(parent_id=parent.session_id)
            assert len(parent_children) == 1
            assert child.session_id in parent_children

            child_children = await manager.list_sessions(parent_id=child.session_id)
            assert len(child_children) == 0
