"""Tests that SQLSessionStore.__aexit__ does not dispose shared engines.

The shared engine cache (get_shared_engine) is a process-level cache.
If SQLSessionStore.__aexit__ disposes the engine, the cache holds a
reference to a disposed engine, breaking all subsequent users.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from agentpool.sessions.models import SessionData
from agentpool_config.storage import SQLStorageConfig, get_shared_engine
from agentpool_storage.session_store import SQLSessionStore


@pytest.fixture(autouse=True)
def _clear_engine_cache():
    """Clear engine cache before and after each test to avoid cross-contamination."""
    from agentpool_config.storage import _engine_cache

    _engine_cache.clear()
    yield
    _engine_cache.clear()


async def test_shared_engine_not_disposed_after_store_exit() -> None:
    """SQLSessionStore.__aexit__ must NOT call dispose() on the shared engine.

    The engine comes from get_shared_engine() which caches it at process level.
    Disposing it corrupts the cache — next caller gets a disposed engine.
    For in-memory SQLite, dispose() loses all data; for PostgreSQL, it kills
    the connection pool.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        config = SQLStorageConfig(url=f"sqlite:///{db_path}")

        # Patch AsyncEngine.dispose at the class level to detect calls
        original_dispose = AsyncEngine.dispose
        dispose_calls: list[AsyncEngine] = []

        async def spy_dispose(self: AsyncEngine) -> None:
            dispose_calls.append(self)
            await original_dispose(self)

        with patch.object(AsyncEngine, "dispose", spy_dispose):
            async with SQLSessionStore(config) as store:
                await store.save(SessionData(session_id="s1", agent_name="a1"))

        # The shared engine should NOT have been disposed
        shared_engine = get_shared_engine(config.url)
        disposed_engines = [e for e in dispose_calls if e is shared_engine]
        assert len(disposed_engines) == 0, (
            f"SQLSessionStore.__aexit__ disposed the shared engine "
            f"(dispose was called {len(disposed_engines)} time(s) on it)"
        )


async def test_sequential_stores_same_config() -> None:
    """Two sequential SQLSessionStore instances using the same config should work.

    This is the real-world scenario from SQLModelProvider, which creates a
    new SQLSessionStore for each session operation. If the first one disposes
    the shared engine on exit, the second one's engine may be broken.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        config = SQLStorageConfig(url=f"sqlite:///{db_path}")

        # First store usage — save a session
        async with SQLSessionStore(config) as store1:
            await store1.save(SessionData(session_id="s1", agent_name="a1"))

        # Second store usage — load the session
        async with SQLSessionStore(config) as store2:
            result = await store2.load("s1")

        assert result is not None
        assert result.session_id == "s1"
        assert result.agent_name == "a1"


async def test_engine_reference_dropped_after_exit() -> None:
    """SQLSessionStore should drop its engine reference on exit without disposing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        config = SQLStorageConfig(url=f"sqlite:///{db_path}")

        store = SQLSessionStore(config)
        assert store._engine is None

        async with store:
            assert store._engine is not None

        # Reference should be cleared
        assert store._engine is None

        # But the shared engine should still be in the cache and usable
        from agentpool_config.storage import _engine_cache

        assert config.url in _engine_cache
        engine = _engine_cache[config.url]
        from sqlalchemy import text

        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
