"""Test fixtures for OpenCode server tests.

Provides fixtures for testing the OpenCode server API, including:
- Real lightweight components where possible (StorageManager, FileOpsTracker, TodoTracker)
- Mock agent and pool (require heavy infrastructure like model clients, MCP servers)
- Server state management
- FastAPI test client setup
- Temporary directory management for git-enabled tests
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
import tempfile
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient
import pytest

from agentpool.models.manifest import AgentsManifest
from agentpool.storage import StorageManager
from agentpool.utils.streams import FileOpsTracker
from agentpool.utils.time_utils import now_ms
from agentpool.utils.todos import TodoTracker
from agentpool_server.opencode_server.dependencies import get_state
from agentpool_server.opencode_server.models import Session
from agentpool_server.opencode_server.models.common import TimeCreatedUpdated
from agentpool_server.opencode_server.routes import agent_router, file_router, session_router
from agentpool_server.opencode_server.routes.global_routes import router as global_router
from agentpool_server.opencode_server.routes.message_routes import router as message_router
from agentpool_server.opencode_server.state import ServerState


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator


# =============================================================================
# Temporary Directory Fixtures (similar to OpenCode's tmpdir)
# =============================================================================


@pytest.fixture
def tmp_project_dir() -> Iterator[Path]:
    """Create a temporary directory for testing.

    Yields the path to a temporary directory that is cleaned up after the test.
    """
    with tempfile.TemporaryDirectory(prefix="opencode-test-") as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def tmp_git_dir(tmp_project_dir: Path) -> Path:
    """Create a temporary directory with git initialized.

    Creates a git repository with an initial empty commit.
    """
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_project_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=tmp_project_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=tmp_project_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "--allow-empty", "-m", "Initial commit"],
        cwd=tmp_project_dir,
        check=True,
        capture_output=True,
    )
    return tmp_project_dir


# =============================================================================
# Real Lightweight Component Fixtures
# =============================================================================


@pytest.fixture
def storage_manager() -> StorageManager:
    """Create a real StorageManager backed by an in-memory provider.

    Uses MemoryStorageProvider so session CRUD, message storage, etc.
    all work without any external dependencies or I/O.
    """
    from agentpool_config.storage import MemoryStorageConfig, StorageConfig

    config = StorageConfig(providers=[MemoryStorageConfig()])
    return StorageManager(config=config)


@pytest.fixture
def file_ops() -> FileOpsTracker:
    """Create a real FileOpsTracker."""
    return FileOpsTracker()


@pytest.fixture
def todos() -> TodoTracker:
    """Create a real TodoTracker."""
    return TodoTracker()


@pytest.fixture
def manifest() -> AgentsManifest:
    """Create a real AgentsManifest with minimal config."""
    return AgentsManifest(config_file_path="/tmp/test-pool")


# =============================================================================
# Mock Fixtures (only for components requiring heavy infrastructure)
# =============================================================================


@pytest.fixture
def mock_pool(
    storage_manager: StorageManager,
    file_ops: FileOpsTracker,
    todos: TodoTracker,
    manifest: AgentsManifest,
) -> Mock:
    """Create a mock agent pool wired to real lightweight components.

    The pool itself must be mocked because a real AgentPool spawns agents,
    MCP servers, and other heavy infrastructure. But its attributes are real
    objects so tests exercise actual storage, file-ops, and todo logic.
    """
    pool = Mock()
    pool.storage = storage_manager
    pool.file_ops = file_ops
    pool.todos = todos
    pool.manifest = manifest
    pool.all_agents = {}
    pool.skill_commands = None
    # Sessions store delegates to the real StorageManager so that
    # create_session's pool.sessions.store.save() persists data that
    # storage.load_session() can retrieve. Without this, the mock
    # absorbs saves and load_session returns None.
    pool.sessions = Mock()
    pool.sessions.store = Mock()
    pool.sessions.store.save = storage_manager.save_session
    pool.sessions.store.delete = storage_manager.delete_session
    pool.sessions.store.load = storage_manager.load_session
    pool.sessions.store.list_sessions = AsyncMock(return_value=[])
    # Mirror the same store on session_pool for the new access path
    pool.session_pool = Mock()

    async def _mock_create_session(
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        **metadata: Any,
    ) -> Mock:
        from datetime import datetime

        from agentpool.sessions.models import SessionData

        data = SessionData(
            session_id=session_id,
            agent_name=agent_name or "test-agent",
            parent_id=parent_session_id,
            created_at=datetime.now(),
            last_active=datetime.now(),
            metadata=metadata,
        )
        await storage_manager.save_session(data)
        return Mock()

    async def _mock_close_session(session_id: str) -> None:
        await storage_manager.delete_session(session_id)

    pool.session_pool.create_session = AsyncMock(side_effect=_mock_create_session)
    pool.session_pool.close_session = AsyncMock(side_effect=_mock_close_session)
    pool.session_pool.sessions = Mock()
    pool.session_pool.sessions.cancel_run_for_session = Mock()
    _mock_session_agent = Mock()
    _mock_session_agent.load_session = AsyncMock(return_value=None)
    _mock_session_agent.conversation = Mock()
    _mock_session_agent.conversation.chat_messages = []
    pool.session_pool.sessions.get_or_create_session_agent = AsyncMock(
        return_value=_mock_session_agent
    )
    pool.session_pool.sessions.get_or_create_session = AsyncMock(
        return_value=(Mock(), True)
    )
    _run_handle = Mock()
    _run_handle.complete_event = Mock()
    _run_handle.complete_event.wait = AsyncMock()
    pool.session_pool.receive_request = AsyncMock(return_value=_run_handle)
    pool.session_pool.event_bus = Mock()
    pool.session_pool.event_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    pool.session_pool.event_bus.unsubscribe = AsyncMock()
    pool.session_pool.sessions.store = Mock()
    pool.session_pool.sessions.store.save = storage_manager.save_session
    pool.session_pool.sessions.store.delete = storage_manager.delete_session
    pool.session_pool.sessions.store.load = storage_manager.load_session
    pool.session_pool.sessions.store.list_sessions = AsyncMock(return_value=[])
    return pool


@pytest.fixture
def mock_env(tmp_project_dir: Path) -> Mock:
    """Create a mock agent environment.

    Uses a real AsyncLocalFileSystem for proper path traversal testing.
    """
    from upathtools.filesystems import AsyncLocalFileSystem

    env = Mock()
    # Use real async filesystem for proper path handling
    fs = AsyncLocalFileSystem()
    env.get_fs = Mock(return_value=fs)
    env.cwd = str(tmp_project_dir)
    env.execute_command = AsyncMock(
        return_value=Mock(success=True, result="command output", error=None)
    )
    return env


@pytest.fixture
def mock_agent(mock_env: Mock, mock_pool: Mock, storage_manager: StorageManager) -> Mock:
    """Create a mock agent for testing.

    The agent must be mocked because a real agent requires model clients,
    tool systems, etc. But its storage attribute is the real StorageManager
    so state.storage (which reads agent.storage) works end-to-end.
    """
    agent = Mock()
    agent.name = "test-agent"
    agent.env = mock_env
    agent._input_provider = None
    agent.run = AsyncMock(return_value=Mock(data="test response"))
    agent.agent_pool = mock_pool
    # Real storage manager (accessed via state.storage -> agent.storage)
    agent.storage = storage_manager

    # Session management methods (used by session routes)
    # list_sessions delegates to storage_manager so that sessions created via
    # pool.sessions.store.save() are visible in GET /session.
    async def _list_sessions(**kwargs: object) -> list[SessionData]:
        from agentpool.sessions.models import SessionData

        ids = await storage_manager.list_session_ids()
        results: list[SessionData] = []
        for sid in ids:
            data = await storage_manager.load_session(sid)
            if data is not None:
                results.append(data)
        return results

    agent.list_sessions = _list_sessions
    agent.load_session = AsyncMock(return_value=None)
    return agent


# =============================================================================
# Server State Fixtures
# =============================================================================


@pytest.fixture
def server_state(tmp_project_dir: Path, mock_agent: Mock) -> ServerState:
    """Create a server state for testing."""
    return ServerState(working_dir=str(tmp_project_dir), agent=mock_agent)


# =============================================================================
# FastAPI Test Client Fixtures
# =============================================================================


@pytest.fixture
def app(server_state: ServerState) -> FastAPI:
    """Create a FastAPI app with all routes for testing."""
    app = FastAPI()
    app.include_router(session_router)
    app.include_router(message_router)
    app.include_router(file_router)
    app.include_router(agent_router)
    app.include_router(global_router)
    app.dependency_overrides[get_state] = lambda: server_state
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    """Create a synchronous test client."""
    return TestClient(app)


@pytest.fixture
async def async_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# =============================================================================
# Event Capture Fixtures
# =============================================================================


class EventCapture:
    """Helper class to capture broadcasted events."""

    def __init__(self) -> None:
        self.events: list[Any] = []
        self._queue: asyncio.Queue[Any] = asyncio.Queue()

    async def capture(self, event: Any) -> None:
        """Capture an event."""
        self.events.append(event)
        await self._queue.put(event)

    def get_events_by_type(self, event_type: str) -> list[Any]:
        """Get all events of a specific type."""
        return [e for e in self.events if e.type == event_type]

    def clear(self) -> None:
        """Clear captured events."""
        self.events.clear()


@pytest.fixture
def event_capture(server_state: ServerState) -> EventCapture:
    """Create an event capture and hook it into the server state."""
    capture = EventCapture()
    # Patch the broadcast_event method to capture events
    original_broadcast = server_state.broadcast_event

    async def capturing_broadcast(event: Any) -> None:
        await capture.capture(event)
        await original_broadcast(event)

    server_state.broadcast_event = capturing_broadcast  # type: ignore[method-assign]
    return capture


# =============================================================================
# SSE Stream Fixtures
# =============================================================================


class SSEStream:
    r"""Async helper for consuming SSE events from the /global/event endpoint.

    Connects via httpx streaming, parses ``data: {json}\n\n`` lines,
    and exposes parsed events through an async queue.
    """

    def __init__(self, client: AsyncClient) -> None:
        self._client = client
        self._queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def connect(self) -> None:
        """Connect to SSE endpoint and start consuming events."""
        self._task = asyncio.create_task(self._consume())

    async def _consume(self) -> None:
        """Background task that reads SSE events and puts them in queue."""
        async with self._client.stream("GET", "/global/event") as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    event_data = json.loads(line[6:])
                    await self._queue.put(event_data)
                elif line.startswith(": "):
                    continue  # SSE comment / keepalive

    async def next_event(self, timeout: float = 5.0) -> dict[str, Any]:
        """Get next parsed SSE event with timeout."""
        return await asyncio.wait_for(self._queue.get(), timeout=timeout)

    async def aclose(self) -> None:
        """Close the SSE stream."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task


@pytest.fixture
async def global_event_stream(async_client: AsyncClient) -> AsyncIterator[SSEStream]:
    """Create an SSE stream consumer for /global/event endpoint.

    Automatically connects and consumes the initial ``server.connected``
    event before yielding.
    """
    stream = SSEStream(async_client)
    await stream.connect()
    # Consume the initial server.connected event
    connected = await stream.next_event(timeout=5.0)
    assert connected.get("type") == "server.connected"
    yield stream
    await stream.aclose()


def parse_sse_event(line: str) -> dict[str, Any]:
    """Parse a single SSE data line into a dict.

    Args:
        line: Raw SSE line, e.g. ``data: {"type": "server.connected"}``

    Returns:
        Parsed JSON dict from the data payload.
    """
    if line.startswith("data: "):
        return json.loads(line[6:])
    return json.loads(line)


# =============================================================================
# Session Factory Fixtures
# =============================================================================


@pytest.fixture
def session_factory(tmp_project_dir: Path):
    """Factory for creating test sessions."""

    def create_session(
        session_id: str = "test-session-001",
        title: str = "Test Session",
        project_id: str = "default",
    ) -> Session:
        now = now_ms()
        return Session(
            id=session_id,
            project_id=project_id,
            directory=str(tmp_project_dir),
            title=title,
            version="1",
            time=TimeCreatedUpdated(created=now, updated=now),
        )

    return create_session
