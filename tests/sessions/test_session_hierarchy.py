"""Tests for session hierarchy functionality."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator import SessionPool
from agentpool.sessions import SessionData
from agentpool.sessions.store import MemorySessionStore
from agentpool.utils.identifiers import generate_session_id
from agentpool_config.storage import SQLStorageConfig
from agentpool_storage.session_store import SQLSessionStore


if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def mock_pool() -> MagicMock:
    """Create a mock pool with manifest."""
    pool = MagicMock()
    pool.manifest.name = "test_pool"
    return pool


@pytest.fixture
def memory_store() -> MemorySessionStore:
    """Create a memory session store for testing."""
    return MemorySessionStore()


@pytest.fixture
def sql_store(tmp_path: Path) -> SQLSessionStore:
    """Create a SQL session store with temp database."""
    db_path = tmp_path / "test_hierarchy.db"
    config = SQLStorageConfig(url=f"sqlite:///{db_path}")
    return SQLSessionStore(config)


class TestSessionHierarchy:
    """Tests for session parent-child hierarchy."""

    async def test_create_with_parent_id(
        self, mock_pool: MagicMock, memory_store: MemorySessionStore
    ) -> None:
        """Test that parent_id is persisted correctly via create_session."""
        session_pool = SessionPool(pool=mock_pool, store=memory_store)
        await session_pool.start()

        # Create parent session directly in store
        parent = SessionData(session_id="parent_1", agent_name="coordinator")
        await memory_store.save(parent)

        # Create child session via session pool
        child_state = await session_pool.create_session(
            session_id=generate_session_id(),
            agent_name="coder",
            parent_session_id="parent_1",
        )
        child_id = child_state.session_id

        # Verify child has parent_id
        child = await memory_store.load(child_id)
        assert child is not None
        assert child.parent_id == "parent_1"

        # Verify when loaded again
        loaded = await memory_store.load(child_id)
        assert loaded is not None
        assert loaded.parent_id == "parent_1"

    async def test_list_by_parent_id_memory(
        self, mock_pool: MagicMock, memory_store: MemorySessionStore
    ) -> None:
        """Test filtering sessions by parent_id with memory store."""
        session_pool = SessionPool(pool=mock_pool, store=memory_store)
        await session_pool.start()

        # Create root and parent sessions directly
        root = SessionData(session_id="root_1", agent_name="root_agent")
        await memory_store.save(root)

        parent = SessionData(session_id="parent_1", agent_name="parent_agent")
        await memory_store.save(parent)

        # Create child sessions via session pool
        child1_state = await session_pool.create_session(
            session_id=generate_session_id(),
            agent_name="child_agent",
            parent_session_id="parent_1",
        )
        child2_state = await session_pool.create_session(
            session_id=generate_session_id(),
            agent_name="child_agent2",
            parent_session_id="parent_1",
        )
        child1_id = child1_state.session_id
        child2_id = child2_state.session_id

        # List children of parent via store
        children = await memory_store.list_sessions(parent_id="parent_1")

        # Verify only children of parent are returned
        assert len(children) == 2
        assert child1_id in children
        assert child2_id in children
        assert "root_1" not in children
        assert "parent_1" not in children

    async def test_list_by_parent_id_sql(
        self, mock_pool: MagicMock, sql_store: SQLSessionStore
    ) -> None:
        """Test filtering sessions by parent_id with SQL store."""
        session_pool = SessionPool(pool=mock_pool, store=sql_store)

        async with sql_store:
            await session_pool.start()

            # Create root and parent sessions directly
            root = SessionData(session_id="root_1", agent_name="root_agent")
            await sql_store.save(root)

            parent = SessionData(session_id="parent_1", agent_name="parent_agent")
            await sql_store.save(parent)

            # Create child sessions via session pool
            child1_state = await session_pool.create_session(
                session_id=generate_session_id(),
                agent_name="child_agent",
                parent_session_id="parent_1",
            )
            child2_state = await session_pool.create_session(
                session_id=generate_session_id(),
                agent_name="child_agent2",
                parent_session_id="parent_1",
            )
            child1_id = child1_state.session_id
            child2_id = child2_state.session_id

            # List children of parent via store
            children = await sql_store.list_sessions(parent_id="parent_1")

            # Verify only children of parent are returned
            assert len(children) == 2
            assert child1_id in children
            assert child2_id in children
            assert "root_1" not in children
            assert "parent_1" not in children

    async def test_create_with_invalid_parent(
        self, mock_pool: MagicMock, memory_store: MemorySessionStore
    ) -> None:
        """Test that creating with non-existent parent_id succeeds (permissive)."""
        session_pool = SessionPool(pool=mock_pool, store=memory_store)
        await session_pool.start()

        # Create child with fake parent_id
        child_state = await session_pool.create_session(
            session_id=generate_session_id(),
            agent_name="agent",
            parent_session_id="nonexistent_parent_id",
        )
        child_id = child_state.session_id

        # Should succeed (permissive validation)
        child = await memory_store.load(child_id)
        assert child is not None
        assert child.parent_id == "nonexistent_parent_id"

        # Verify persisted correctly
        loaded = await memory_store.load(child_id)
        assert loaded is not None
        assert loaded.parent_id == "nonexistent_parent_id"

    async def test_list_by_parent_id_with_no_children(
        self, mock_pool: MagicMock, memory_store: MemorySessionStore
    ) -> None:
        """Test filtering by parent_id returns empty list when no children exist."""
        session_pool = SessionPool(pool=mock_pool, store=memory_store)
        await session_pool.start()

        # Create parent but no children
        await memory_store.save(SessionData(session_id="parent_1", agent_name="root_agent"))
        await memory_store.save(SessionData(session_id="other_1", agent_name="other_agent"))

        # List children of non-existent parent via session pool controller
        children = session_pool.sessions.get_children("nonexistent_parent")

        # Should return empty list
        assert len(children) == 0

    async def test_nested_hierarchy(
        self, mock_pool: MagicMock, memory_store: MemorySessionStore
    ) -> None:
        """Test multi-level hierarchy (grandparent -> parent -> child)."""
        session_pool = SessionPool(pool=mock_pool, store=memory_store)
        await session_pool.start()

        # Create grandparent session directly
        grandparent = SessionData(session_id="gp_1", agent_name="root_agent")
        await memory_store.save(grandparent)

        # Create parent as child of grandparent
        parent_state = await session_pool.create_session(
            session_id=generate_session_id(),
            agent_name="parent_agent",
            parent_session_id="gp_1",
        )
        parent_id = parent_state.session_id

        # Create child as child of parent
        child_state = await session_pool.create_session(
            session_id=generate_session_id(),
            agent_name="child_agent",
            parent_session_id=parent_id,
        )
        child_id = child_state.session_id

        # Verify hierarchy through list operations
        grandparent_children = await memory_store.list_sessions(parent_id="gp_1")
        assert len(grandparent_children) == 1
        assert parent_id in grandparent_children

        parent_children = await memory_store.list_sessions(parent_id=parent_id)
        assert len(parent_children) == 1
        assert child_id in parent_children

        child_children = await memory_store.list_sessions(parent_id=child_id)
        assert len(child_children) == 0
