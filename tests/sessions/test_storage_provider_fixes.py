"""Tests for storage provider fixes.

Covers:
- SQLProvider.log_session() duplicate session handling
- OpenCodeStorageProvider.load_session() implementation
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from agentpool.sessions import SessionData
from agentpool.utils.identifiers import ascending
from agentpool_config.storage import OpenCodeStorageConfig, SQLStorageConfig
from agentpool_storage.opencode_provider import OpenCodeStorageProvider
from agentpool_storage.sql_provider import SQLModelProvider


if TYPE_CHECKING:
    pass


class TestSQLProviderLogSession:
    """Tests for SQLProvider.log_session() duplicate handling."""

    @pytest.fixture
    async def provider(self):
        """Create a SQL provider with temp database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            config = SQLStorageConfig(url=f"sqlite:///{db_path}")
            provider = SQLModelProvider(config)
            async with provider:
                yield provider

    async def test_log_session_duplicate_idempotent(self, provider: SQLModelProvider) -> None:
        """Test that log_session is idempotent - calling twice with same session_id should not raise."""
        session_id = "test_session_001"
        node_name = "test_agent"

        # First call should succeed
        await provider.log_session(
            session_id=session_id,
            node_name=node_name,
        )

        # Second call with same session_id should not raise UNIQUE constraint error
        # It should silently skip the insertion
        await provider.log_session(
            session_id=session_id,
            node_name=node_name,
        )

        # Verify session exists
        sessions = await provider.list_session_ids()
        assert session_id in sessions

    async def test_log_session_different_sessions(self, provider: SQLModelProvider) -> None:
        """Test that different sessions can be logged."""
        session_id_1 = "test_session_001"
        session_id_2 = "test_session_002"

        await provider.log_session(session_id=session_id_1, node_name="agent1")
        await provider.log_session(session_id=session_id_2, node_name="agent2")

        sessions = await provider.list_session_ids()
        assert session_id_1 in sessions
        assert session_id_2 in sessions


class TestOpenCodeStorageProviderPathHandling:
    """Tests for OpenCodeStorageProvider path handling."""

    async def test_db_path_converted_to_storage_dir(self) -> None:
        """Test that .db file path is converted to storage directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Configure with .db file path (legacy config style)
            db_path = Path(tmpdir) / "opencode.db"
            config = OpenCodeStorageConfig(path=str(db_path))
            provider = OpenCodeStorageProvider(config)

            # Provider should use storage directory, not the .db file path
            expected_base = Path(tmpdir) / "storage"
            assert provider.base_path == expected_base
            assert provider.sessions_path == expected_base / "session"
            assert provider.messages_path == expected_base / "message"
            assert provider.parts_path == expected_base / "part"

    async def test_directory_path_used_directly(self) -> None:
        """Test that directory path is used as-is."""
        with tempfile.TemporaryDirectory() as tmpdir:
            storage_path = Path(tmpdir) / "storage"
            config = OpenCodeStorageConfig(path=str(storage_path))
            provider = OpenCodeStorageProvider(config)

            # Provider should use the directory path directly
            assert provider.base_path == storage_path
            assert provider.sessions_path == storage_path / "session"
            assert provider.messages_path == storage_path / "message"
            assert provider.parts_path == storage_path / "part"

    """Tests for OpenCodeStorageProvider.load_session() implementation."""

    @pytest.fixture
    async def provider(self):
        """Create an OpenCode provider with temp directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = OpenCodeStorageConfig(path=tmpdir)
            provider = OpenCodeStorageProvider(config)
            async with provider:
                yield provider

    async def test_load_session_not_found(self, provider: OpenCodeStorageProvider) -> None:
        """Test loading a non-existent session returns None."""
        result = await provider.load_session("non_existent_session")
        assert result is None

    async def test_load_session_returns_session_data(
        self, provider: OpenCodeStorageProvider
    ) -> None:
        """Test that load_session returns SessionData for existing session."""
        # Use ascending() to generate unique IDs
        from agentpool_server.opencode_server.models import (
            Session,
            SessionSummary,
            TimeCreatedUpdated,
        )

        # Create a session manually
        session_id = ascending("session")
        project_id = "test_project"

        # Create session directory and file
        session_dir = provider.sessions_path / project_id
        session_dir.mkdir(parents=True, exist_ok=True)

        now_ms = 1234567890000
        oc_session = Session(
            id=session_id,
            project_id=project_id,
            directory="/test/dir",
            title="Test Session",
            version="1.1.7",
            time=TimeCreatedUpdated(created=now_ms, updated=now_ms),
            summary=SessionSummary(files=0, additions=0, deletions=0),
        )

        import anyenv

        session_file = session_dir / f"{session_id}.json"
        session_file.write_text(anyenv.dump_json(oc_session.model_dump(by_alias=True), indent=True))

        # Create message directory (empty)
        (provider.messages_path / session_id).mkdir(parents=True, exist_ok=True)

        # Load the session
        result = await provider.load_session(session_id)

        # Verify result
        assert result is not None
        assert isinstance(result, SessionData)
        assert result.session_id == session_id
        assert result.project_id == project_id
        assert result.cwd == "/test/dir"

    async def test_delete_session(self, provider: OpenCodeStorageProvider) -> None:
        """Test that delete_session removes session and all data."""
        # Use ascending() to generate unique IDs
        from agentpool_server.opencode_server.models import (
            Session,
            SessionSummary,
            TimeCreatedUpdated,
        )

        session_id = ascending("session")
        project_id = "test_project"

        # Create session
        session_dir = provider.sessions_path / project_id
        session_dir.mkdir(parents=True, exist_ok=True)

        oc_session = Session(
            id=session_id,
            project_id=project_id,
            directory="/test/dir",
            title="Test Session",
            version="1.1.7",
            time=TimeCreatedUpdated(created=1234567890000, updated=1234567890000),
            summary=SessionSummary(files=0, additions=0, deletions=0),
        )

        import anyenv

        session_file = session_dir / f"{session_id}.json"
        session_file.write_text(anyenv.dump_json(oc_session.model_dump(by_alias=True), indent=True))
        (provider.messages_path / session_id).mkdir(parents=True, exist_ok=True)
        (provider.parts_path / session_id).mkdir(parents=True, exist_ok=True)

        # Verify session exists
        assert (provider.messages_path / session_id).exists()

        # Delete session
        result = await provider.delete_session(session_id)
        assert result is True

        # Verify session is deleted
        result = await provider.load_session(session_id)
        assert result is None
        assert not (provider.messages_path / session_id).exists()

    async def test_list_session_ids(self, provider: OpenCodeStorageProvider) -> None:
        """Test that list_session_ids returns all session IDs."""
        # Use ascending() to generate unique IDs
        from agentpool_server.opencode_server.models import (
            Session,
            SessionSummary,
            TimeCreatedUpdated,
        )

        import anyenv

        session_ids = []
        for i in range(3):
            session_id = ascending("session")
            session_ids.append(session_id)
            project_id = "test_project"

            session_dir = provider.sessions_path / project_id
            session_dir.mkdir(parents=True, exist_ok=True)

            oc_session = Session(
                id=session_id,
                project_id=project_id,
                directory=f"/test/dir{i}",
                title=f"Test Session {i}",
                version="1.1.7",
                time=TimeCreatedUpdated(created=1234567890000 + i, updated=1234567890000 + i),
                summary=SessionSummary(files=0, additions=0, deletions=0),
            )

            session_file = session_dir / f"{session_id}.json"
            session_file.write_text(
                anyenv.dump_json(oc_session.model_dump(by_alias=True), indent=True)
            )
            (provider.messages_path / session_id).mkdir(parents=True, exist_ok=True)

        # List sessions
        result = await provider.list_session_ids()

        # Verify all sessions are listed
        for session_id in session_ids:
            assert session_id in result
