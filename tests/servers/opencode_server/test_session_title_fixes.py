"""Pytest tests for session title persistence fixes.

These tests verify the fixes for:
1. converters.py: opencode_to_session_data saves title to metadata
2. session_store.py: SQL storage converters handle title field
3. storage/manager.py: _generate_title_from_prompt stores generated title
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import patch

import anyio
import pytest

from agentpool.sessions.models import SessionData
from agentpool.storage.manager import SessionMetadata, StorageManager
from agentpool_server.opencode_server.converters import (
    opencode_to_session_data,
    session_data_to_opencode,
)
from agentpool_server.opencode_server.models import Session
from agentpool_server.opencode_server.models.common import TimeCreatedUpdated
from agentpool_config.storage import MemoryStorageConfig, SQLStorageConfig, StorageConfig
from agentpool_storage.memory_provider.provider import MemoryStorageProvider
from agentpool_storage.sql_provider.sql_provider import SQLModelProvider
from agentpool_storage.session_store import SQLSessionStore


if TYPE_CHECKING:
    from pathlib import Path


class TestConvertersTitleFix:
    """Tests for converters.py title persistence fix."""

    def test_opencode_to_session_data_saves_title(self) -> None:
        """Verify opencode_to_session_data saves title to metadata."""
        session = Session(
            id="test_session_001",
            project_id="global",
            directory="/tmp",
            title="My Test Title",
            version="1",
            time=TimeCreatedUpdated(created=1234567890, updated=1234567890),
        )

        session_data = opencode_to_session_data(session, agent_name="test_agent")

        # Title should be in metadata
        assert "title" in session_data.metadata
        assert session_data.metadata["title"] == "My Test Title"
        # Title property should also work
        assert session_data.title == "My Test Title"

    def test_opencode_to_session_data_empty_title(self) -> None:
        """Verify opencode_to_session_data handles empty title correctly."""
        session = Session(
            id="test_session_002",
            project_id="global",
            directory="/tmp",
            title="",  # Empty title
            version="1",
            time=TimeCreatedUpdated(created=1234567890, updated=1234567890),
        )

        session_data = opencode_to_session_data(session, agent_name="test_agent")

        # Empty title should not be added to metadata
        assert session_data.metadata.get("title") in (None, "")

    def test_session_data_to_opencode_reads_title(self) -> None:
        """Verify session_data_to_opencode reads title from metadata."""
        session_data = SessionData(
            session_id="test_session_003",
            agent_name="test_agent",
            metadata={"title": "Metadata Title"},
        )

        session = session_data_to_opencode(session_data)

        assert session.title == "Metadata Title"

    def test_session_data_to_opencode_default_title(self) -> None:
        """Verify session_data_to_opencode uses default when no title in metadata."""
        session_data = SessionData(
            session_id="test_session_004",
            agent_name="test_agent",
            metadata={},  # No title
        )

        session = session_data_to_opencode(session_data)

        assert session.title == "New Session"  # Default value

    def test_roundtrip_conversion_preserves_title(self) -> None:
        """Verify round-trip conversion preserves title."""
        original = Session(
            id="test_roundtrip",
            project_id="global",
            directory="/tmp",
            title="Round Trip Title",
            version="1",
            time=TimeCreatedUpdated(created=1234567890, updated=1234567890),
        )

        # Convert to SessionData and back
        session_data = opencode_to_session_data(original, agent_name="test_agent")
        converted = session_data_to_opencode(session_data)

        assert converted.title == "Round Trip Title"


class TestSQLSessionStoreTitleFix:
    """Tests for session_store.py title field handling."""

    @pytest.fixture
    def sql_config(self, tmp_path: Path) -> SQLStorageConfig:
        """Create SQL config with temp database."""
        db_path = tmp_path / "test_title_fix.db"
        return SQLStorageConfig(url=f"sqlite:///{db_path}")

    async def test_sql_to_db_model_includes_title(self, sql_config: SQLStorageConfig) -> None:
        """Verify _to_db_model includes title field."""
        async with SQLSessionStore(sql_config) as store:
            session_data = SessionData(
                session_id="sql_test_001",
                agent_name="test_agent",
                metadata={"title": "SQL Stored Title", "custom_key": "value"},
            )

            # Convert to DB model
            db_model = store._to_db_model(session_data)

            # Title should be in DB model
            assert db_model.title == "SQL Stored Title"
            # Metadata should still have other keys in metadata_json
            assert "custom_key" in db_model.metadata_json

    async def test_sql_from_db_model_includes_title(self, sql_config: SQLStorageConfig) -> None:
        """Verify _from_db_model includes title in metadata."""
        async with SQLSessionStore(sql_config) as store:
            # Create and save a session with title
            session_data = SessionData(
                session_id="sql_test_002",
                agent_name="test_agent",
                metadata={"title": "Original Title"},
            )
            await store.save(session_data)

            # Load it back
            loaded = await store.load("sql_test_002")

            assert loaded is not None
            # Title should be in metadata
            assert "title" in loaded.metadata
            assert loaded.metadata["title"] == "Original Title"
            # Title property should work
            assert loaded.title == "Original Title"

    async def test_sql_save_load_roundtrip_title(self, sql_config: SQLStorageConfig) -> None:
        """Verify title survives SQL save/load roundtrip."""
        async with SQLSessionStore(sql_config) as store:
            original = SessionData(
                session_id="sql_test_003",
                agent_name="test_agent",
                metadata={"title": "Round Trip Title", "other": "data"},
            )

            # Save
            await store.save(original)

            # Load
            loaded = await store.load("sql_test_003")

            assert loaded is not None
            assert loaded.title == "Round Trip Title"
            assert loaded.metadata.get("other") == "data"


class TestStorageManagerTitleGenerationFix:
    """Tests for storage/manager.py title generation fix."""

    async def test_generate_title_from_prompt_stores_title(self) -> None:
        """Verify _generate_title_from_prompt stores the generated title."""
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )

        async with StorageManager(config) as manager:
            session_id = "gen_test_001"

            # Create session first
            await manager.log_session(session_id=session_id, node_name="test_agent")

            # Mock title generation
            mock_metadata = SessionMetadata(
                title="Generated Title",
                emoji="🧪",
                icon="mdi:test-tube",
            )

            with patch.object(
                StorageManager,
                "_generate_title_core",
                return_value=mock_metadata,
            ):
                # Generate title
                title = await manager._generate_title_from_prompt(
                    session_id=session_id,
                    prompt="Test prompt",
                    on_title_generated=None,
                )

                # Should return the title
                assert title == "Generated Title"

                # Title should be stored
                stored = await manager.get_session_title(session_id)
                assert stored == "Generated Title"

    async def test_generate_title_from_prompt_calls_callback(self) -> None:
        """Verify _generate_title_from_prompt calls callback with title."""
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )

        async with StorageManager(config) as manager:
            session_id = "gen_test_002"
            await manager.log_session(session_id=session_id, node_name="test_agent")

            callback_called = False
            received_title = None

            def callback(title: str) -> None:
                nonlocal callback_called, received_title
                callback_called = True
                received_title = title

            mock_metadata = SessionMetadata(
                title="Callback Title",
                emoji="📞",
                icon="mdi:phone",
            )

            with patch.object(
                StorageManager,
                "_generate_title_core",
                return_value=mock_metadata,
            ):
                await manager._generate_title_from_prompt(
                    session_id=session_id,
                    prompt="Another prompt",
                    on_title_generated=callback,
                )

                assert callback_called
                assert received_title == "Callback Title"

    async def test_generate_title_from_prompt_skips_if_exists(self) -> None:
        """Verify _generate_title_from_prompt skips generation if title exists."""
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )

        async with StorageManager(config) as manager:
            session_id = "gen_test_003"
            await manager.log_session(session_id=session_id, node_name="test_agent")

            # Set existing title
            await manager.update_session_title(session_id, "Existing Title")

            # Try to generate - should return existing without calling core
            with patch.object(
                StorageManager,
                "_generate_title_core",
            ) as mock_core:
                title = await manager._generate_title_from_prompt(
                    session_id=session_id,
                    prompt="New prompt",
                    on_title_generated=None,
                )

                assert title == "Existing Title"
                mock_core.assert_not_called()  # Should not generate

    async def test_generate_title_from_prompt_generates_despite_default_title(self) -> None:
        """Verify _generate_title_from_prompt generates title even when default 'New Session' exists.

        Regression test: 'New Session' is the default placeholder title assigned
        when a session is created.  Title generation should NOT be blocked by
        this default value — it should proceed and replace it with an LLM-generated title.
        """
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )

        async with StorageManager(config) as manager:
            session_id = "gen_test_new_session_default"
            await manager.log_session(session_id=session_id, node_name="test_agent")

            # Set the default placeholder title (simulates what create_session does)
            await manager.update_session_title(session_id, "New Session")

            mock_metadata = SessionMetadata(
                title="Generated Title",
                emoji="🧪",
                icon="mdi:test-tube",
            )

            with patch.object(
                StorageManager,
                "_generate_title_core",
                return_value=mock_metadata,
            ) as mock_core:
                title = await manager._generate_title_from_prompt(
                    session_id=session_id,
                    prompt="Test prompt",
                    on_title_generated=None,
                )

                # Should have called _generate_title_core (not skipped)
                mock_core.assert_called_once()
                # Should return the generated title, not "New Session"
                assert title == "Generated Title"
                # Title should be stored
                stored = await manager.get_session_title(session_id)
                assert stored == "Generated Title"

    async def test_log_session_triggers_generation(self) -> None:
        """Verify log_session triggers title generation with initial_prompt."""
        config = StorageConfig(
            providers=[MemoryStorageConfig()],
            title_generation_model="test",
        )

        async with StorageManager(config) as manager:
            session_id = "log_test_001"
            callback_called = False

            def callback(title: str) -> None:
                nonlocal callback_called
                callback_called = True

            mock_metadata = SessionMetadata(
                title="Auto Title",
                emoji="🤖",
                icon="mdi:robot",
            )

            with patch.object(
                StorageManager,
                "_generate_title_core",
                return_value=mock_metadata,
            ):
                # Remove pytest env to trigger generation
                with patch.dict(os.environ, {}, clear=False):
                    os.environ.pop("PYTEST_CURRENT_TEST", None)

                    await manager.log_session(
                        session_id=session_id,
                        node_name="test_agent",
                        initial_prompt="Generate a title for this",
                        on_title_generated=callback,
                    )

                    # Wait for async processing
                    await anyio.sleep(0.1)

            # Title should be generated and stored
            stored = await manager.get_session_title(session_id)
            assert stored == "Auto Title"
            assert callback_called


class TestMemoryProviderTitleOperations:
    """Tests for MemoryStorageProvider title operations."""

    async def test_memory_provider_update_and_get_title(self) -> None:
        """Verify MemoryStorageProvider update/get title works."""
        config = MemoryStorageConfig()
        provider = MemoryStorageProvider(config)

        session_id = "mem_test_001"
        await provider.log_session(session_id=session_id, node_name="test_agent")

        # Initially no title
        assert await provider.get_session_title(session_id) is None

        # Update title
        await provider.update_session_title(session_id, "Memory Title")

        # Get title
        assert await provider.get_session_title(session_id) == "Memory Title"


class TestOpenCodeProviderTitleFix:
    """Tests for OpenCodeStorageProvider title persistence fix."""

    async def test_save_session_creates_new_session_file(self, tmp_path: Path) -> None:
        """Verify save_session creates new session file when it doesn't exist."""
        from agentpool_storage.opencode_provider.provider import OpenCodeStorageProvider
        from agentpool_config.storage import OpenCodeStorageConfig

        config = OpenCodeStorageConfig(path=str(tmp_path / "opencode_storage"))
        provider = OpenCodeStorageProvider(config)

        # Create SessionData with title
        session_data = SessionData(
            session_id="new_session_001",
            agent_name="test_agent",
            project_id="test_project",
            cwd="/tmp/test",
            metadata={"title": "New Session Title"},
        )

        # Save session (should create new file)
        await provider.save_session(session_data)

        # Verify session file was created
        session_file = provider.sessions_path / "test_project" / "new_session_001.json"
        assert session_file.exists(), f"Session file should be created at {session_file}"

        # Verify title was saved
        from agentpool_storage.opencode_provider import helpers

        oc_session = helpers.read_session(session_file)
        assert oc_session is not None
        title = oc_session.title
        assert title == "New Session Title"
        assert oc_session.id == "new_session_001"
        assert oc_session.project_id == "test_project"

    async def test_save_session_updates_existing_session(self, tmp_path: Path) -> None:
        """Verify save_session updates existing session file."""
        from agentpool_storage.opencode_provider.provider import OpenCodeStorageProvider
        from agentpool_config.storage import OpenCodeStorageConfig

        config = OpenCodeStorageConfig(path=str(tmp_path / "opencode_storage"))
        provider = OpenCodeStorageProvider(config)

        # Create initial session
        session_data = SessionData(
            session_id="update_session_001",
            agent_name="test_agent",
            project_id="test_project",
            metadata={"title": "Initial Title"},
        )
        await provider.save_session(session_data)

        # Update title
        updated_data = SessionData(
            session_id="update_session_001",
            agent_name="test_agent",
            project_id="test_project",
            metadata={"title": "Updated Title"},
        )
        await provider.save_session(updated_data)

        # Verify title was updated
        session_file = provider.sessions_path / "test_project" / "update_session_001.json"
        from agentpool_storage.opencode_provider import helpers

        oc_session = helpers.read_session(session_file)
        assert oc_session is not None
        title = oc_session.title
        assert title == "Updated Title"

    async def test_load_session_reads_title(self, tmp_path: Path) -> None:
        """Verify load_session reads title from session file."""
        from agentpool_storage.opencode_provider.provider import OpenCodeStorageProvider
        from agentpool_config.storage import OpenCodeStorageConfig

        config = OpenCodeStorageConfig(path=str(tmp_path / "opencode_storage"))
        provider = OpenCodeStorageProvider(config)

        # Create session with title
        session_data = SessionData(
            session_id="load_session_001",
            agent_name="test_agent",
            project_id="test_project",
            metadata={"title": "Loadable Title"},
        )
        await provider.save_session(session_data)

        # Load session
        loaded = await provider.load_session("load_session_001")

        assert loaded is not None
        assert loaded.title == "Loadable Title"
        assert loaded.metadata.get("title") == "Loadable Title"


class TestSQLProviderTitleOperations:
    """Tests for SQLModelProvider title operations."""

    @pytest.fixture
    def sql_config(self, tmp_path: Path) -> SQLStorageConfig:
        """Create SQL config with temp database."""
        db_path = tmp_path / "test_sql_title.db"
        return SQLStorageConfig(url=f"sqlite:///{db_path}")

    async def test_sql_provider_update_and_get_title(self, sql_config: SQLStorageConfig) -> None:
        """Verify SQLModelProvider update/get title works."""
        async with SQLModelProvider(sql_config) as provider:
            session_id = "sql_prov_test_001"
            await provider.log_session(session_id=session_id, node_name="test_agent")

            # Initially no title
            assert await provider.get_session_title(session_id) is None

            # Update title
            await provider.update_session_title(session_id, "SQL Provider Title")

            # Get title
            assert await provider.get_session_title(session_id) == "SQL Provider Title"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
