"""Tests that SQLModelProvider session methods use self.engine directly.

Bug: save_session, load_session, delete_session, list_session_ids each create a
new SQLSessionStore(self.config) instance, which runs __aenter__ (migrations +
create_all) every call and __aexit__ (engine.dispose()) which destroys the
shared engine from get_shared_engine() cache.

For GET /session listing N sessions this means N+1 engine dispose+reinit cycles.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from agentpool.sessions import SessionData
from agentpool_config.storage import SQLStorageConfig
from agentpool_storage.sql_provider import SQLModelProvider


if TYPE_CHECKING:
    from pathlib import Path


class TestSQLProviderNoSQLSessionStore:
    """Assert SQLModelProvider session methods never instantiate SQLSessionStore."""

    @pytest.fixture
    def provider(self, tmp_path: Path) -> SQLModelProvider:
        """Create a SQL provider with temp database."""
        db_path = tmp_path / "test_no_dispose.db"
        config = SQLStorageConfig(url=f"sqlite:///{db_path}")
        return SQLModelProvider(config)

    async def test_save_session_does_not_create_sql_session_store(
        self, provider: SQLModelProvider
    ) -> None:
        """save_session should use self.engine, not SQLSessionStore."""
        data = SessionData(session_id="s1", agent_name="agent1")
        async with provider:
            with patch(
                "agentpool_storage.session_store.SQLSessionStore",
                autospec=True,
            ) as mock_store_cls:
                await provider.save_session(data)
                mock_store_cls.assert_not_called()

    async def test_load_session_does_not_create_sql_session_store(
        self, provider: SQLModelProvider
    ) -> None:
        """load_session should use self.engine, not SQLSessionStore."""
        data = SessionData(session_id="s1", agent_name="agent1")
        async with provider:
            await provider.save_session(data)
            with patch(
                "agentpool_storage.session_store.SQLSessionStore",
                autospec=True,
            ) as mock_store_cls:
                await provider.load_session("s1")
                mock_store_cls.assert_not_called()

    async def test_delete_session_does_not_create_sql_session_store(
        self, provider: SQLModelProvider
    ) -> None:
        """delete_session should use self.engine, not SQLSessionStore."""
        data = SessionData(session_id="s1", agent_name="agent1")
        async with provider:
            await provider.save_session(data)
            with patch(
                "agentpool_storage.session_store.SQLSessionStore",
                autospec=True,
            ) as mock_store_cls:
                await provider.delete_session("s1")
                mock_store_cls.assert_not_called()

    async def test_list_session_ids_does_not_create_sql_session_store(
        self, provider: SQLModelProvider
    ) -> None:
        """list_session_ids should use self.engine, not SQLSessionStore."""
        data = SessionData(session_id="s1", agent_name="agent1")
        async with provider:
            await provider.save_session(data)
            with patch(
                "agentpool_storage.session_store.SQLSessionStore",
                autospec=True,
            ) as mock_store_cls:
                await provider.list_session_ids()
                mock_store_cls.assert_not_called()


class TestSQLProviderEngineNotDisposed:
    """Assert repeated session method calls do not dispose the shared engine."""

    @pytest.fixture
    def provider(self, tmp_path: Path) -> SQLModelProvider:
        """Create a SQL provider with temp database."""
        db_path = tmp_path / "test_engine_not_disposed.db"
        config = SQLStorageConfig(url=f"sqlite:///{db_path}")
        return SQLModelProvider(config)

    async def test_multiple_loads_do_not_dispose_engine(self, provider: SQLModelProvider) -> None:
        """Calling load_session multiple times must not dispose self.engine."""
        data = SessionData(session_id="s1", agent_name="agent1")
        async with provider:
            await provider.save_session(data)
            # Load multiple times — if engine were disposed between calls,
            # subsequent loads would fail.
            for _ in range(5):
                loaded = await provider.load_session("s1")
                assert loaded is not None
                assert loaded.session_id == "s1"

    async def test_engine_usable_after_multiple_session_ops(
        self, provider: SQLModelProvider
    ) -> None:
        """Engine must remain usable after save/load/delete/list cycles."""
        async with provider:
            # Save
            await provider.save_session(SessionData(session_id="s1", agent_name="a1", pool_id="p1"))
            await provider.save_session(SessionData(session_id="s2", agent_name="a2", pool_id="p1"))

            # Load
            loaded = await provider.load_session("s1")
            assert loaded is not None

            # List
            ids = await provider.list_session_ids(pool_id="p1")
            assert len(ids) == 2

            # Delete
            deleted = await provider.delete_session("s1")
            assert deleted is True

            # Engine still works for another query
            loaded2 = await provider.load_session("s2")
            assert loaded2 is not None
