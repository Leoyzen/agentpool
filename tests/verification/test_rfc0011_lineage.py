import logging

from pydantic_ai.models.test import TestModel
import pytest
from sqlalchemy import select

from agentpool import Agent, AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.events import RunStartedEvent, SubAgentEvent
from agentpool_config.storage import SQLStorageConfig, StorageConfig
from agentpool_storage.sql_provider import SQLModelProvider
from agentpool_storage.sql_provider.models import Conversation
from agentpool_toolsets.builtin.subagent_tools import SubagentTools


@pytest.fixture
async def sql_provider(tmp_path):
    """Create SQLModelProvider instance with file-based SQLite."""
    db_path = tmp_path / "test.db"
    # Use auto_migration=False to ensure create_all uses current models
    config = SQLStorageConfig(url=f"sqlite+aiosqlite:///{db_path}", auto_migration=False)
    async with SQLModelProvider(config) as p:
        yield p


@pytest.fixture
async def test_pool(sql_provider):
    """Create a pool with two agents and SQL storage."""
    # We pass the provider config to the manifest
    manifest = AgentsManifest(
        agents={
            "parent": NativeAgentConfig(name="parent", model="test"),
            "child": NativeAgentConfig(name="child", model="test"),
        },
        storage=StorageConfig(providers=[sql_provider.config]),
    )
    async with AgentPool(manifest) as pool:
        # Register subagent tools on parent
        parent = pool.get_agent("parent")
        assert isinstance(parent, Agent)
        parent.tools.add_provider(SubagentTools())

        # Mock models for both
        await parent.set_model(TestModel())
        child = pool.get_agent("child")
        assert isinstance(child, Agent)
        await child.set_model(TestModel(custom_output_text="Child response"))

        yield pool


@pytest.mark.asyncio
async def test_subagent_independent_session(test_pool):
    """Test that subagent runs in independent session with unique ID."""
    parent = test_pool.get_agent("parent")
    child = test_pool.get_agent("child")

    # We want to verify that when parent calls 'task', child gets a new session ID.
    # We can capture the call to run_stream on the child agent.
    original_run_stream = child.run_stream
    child_run_kwargs = []

    async def mocked_run_stream(*args, **kwargs):
        child_run_kwargs.append(kwargs)
        async for event in original_run_stream(*args, **kwargs):
            yield event

    child.run_stream = mocked_run_stream

    # Execute task tool on parent
    ctx = parent.get_context()
    tools = SubagentTools()

    parent_session_id = "parent-session-123"
    parent.session_id = parent_session_id

    # In SubagentTools.task, it calls node.run_stream
    await tools.task(ctx, agent_or_team="child", prompt="Do something", description="test task")

    assert len(child_run_kwargs) == 1
    kwargs = child_run_kwargs[0]

    child_session_id = kwargs.get("session_id")
    assert child_session_id is not None
    assert child_session_id != parent_session_id
    assert kwargs.get("parent_session_id") == parent_session_id

    assert isinstance(child_session_id, str)
    assert len(child_session_id) > 0


@pytest.mark.asyncio
async def test_run_started_event_lineage(test_pool):
    """Test that RunStartedEvent contains parent_session_id."""
    child = test_pool.get_agent("child")
    parent_session_id = "parent-123"

    events = []
    async for event in child.run_stream("hello", parent_session_id=parent_session_id):
        events.append(event)

    run_started = next(e for e in events if isinstance(e, RunStartedEvent))
    assert run_started.parent_session_id == parent_session_id
    assert run_started.session_id == child.session_id


@pytest.mark.asyncio
async def test_subagent_event_lineage(test_pool):
    """Test that SubAgentEvent contains both child_session_id and parent_session_id."""
    parent = test_pool.get_agent("parent")
    child = test_pool.get_agent("child")

    parent_session_id = "parent-456"

    from agentpool_toolsets.builtin.subagent_tools import _stream_task

    ctx = parent.get_context()
    parent.session_id = parent_session_id

    child_session_id = "child-789"

    captured_events = []
    # Mock parent._event_queue.put to capture events
    original_put = parent._event_queue.put

    async def mock_put(event):
        captured_events.append(event)
        await original_put(event)

    parent._event_queue.put = mock_put

    # We need a stream from the child
    child_stream = child.run_stream(
        "child prompt", session_id=child_session_id, parent_session_id=parent_session_id
    )

    await _stream_task(
        ctx,
        source_name="child",
        source_type="agent",
        stream=child_stream,
        child_session_id=child_session_id,
        parent_session_id=parent_session_id,
    )

    subagent_events = [e for e in captured_events if isinstance(e, SubAgentEvent)]
    assert len(subagent_events) > 0
    for e in subagent_events:
        assert e.child_session_id == child_session_id
        assert e.parent_session_id == parent_session_id


@pytest.mark.asyncio
async def test_sql_storage_parent_id(test_pool):
    """Test that SQL storage shows correct parent_id for child session."""
    storage_manager = test_pool.storage
    sql_provider = storage_manager.providers[0]
    assert isinstance(sql_provider, SQLModelProvider)

    parent_id = "parent-session-db"
    child_id = "child-session-db"

    # Log parent session
    await sql_provider.log_session(session_id=parent_id, node_name="parent")

    # Log child session with parent_id
    await sql_provider.log_session(
        session_id=child_id, node_name="child", parent_session_id=parent_id
    )

    # Verify in DB
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(sql_provider.engine) as session:
        result = await session.execute(select(Conversation).where(Conversation.id == child_id))
        convo = result.scalar_one_or_none()
        assert convo is not None
        assert convo.parent_id == parent_id


@pytest.mark.asyncio
async def test_storage_soft_validation(test_pool, caplog):
    """Test that soft validation works (no crash if parent missing)."""
    storage_manager = test_pool.storage
    sql_provider = storage_manager.providers[0]
    assert isinstance(sql_provider, SQLModelProvider)

    caplog.set_level(logging.WARNING)

    child_id = "child-with-ghost-parent"
    ghost_parent_id = "non-existent-parent"

    # This should not raise an exception
    await sql_provider.log_session(
        session_id=child_id, node_name="child", parent_session_id=ghost_parent_id
    )

    # Verify warning in logs
    # Note: structlog might not propagate to caplog easily depending on config,
    # but since it's using stdlib LoggerFactory it should.
    assert any(
        ghost_parent_id in record.message
        for record in caplog.records
        if record.levelname == "WARNING"
    )

    # Verify child still saved
    from sqlalchemy.ext.asyncio import AsyncSession

    async with AsyncSession(sql_provider.engine) as session:
        result = await session.execute(select(Conversation).where(Conversation.id == child_id))
        convo = result.scalar_one()
        assert convo.id == child_id
        assert convo.parent_id == ghost_parent_id
