from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool.agents.events import SpawnSessionStart, StreamCompleteEvent
from agentpool.agents.exceptions import DelegationDepthError, MAX_DELEGATION_DEPTH


if TYPE_CHECKING:
    from pathlib import Path


class StructuredResponse(BaseModel):
    """Test model for structured output."""

    message: str
    value: int


BASIC_WORKERS = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - worker
      - specialist
    system_prompt: "You are the main agent. Use your workers to help with tasks."

  worker:
    type: native
    model: test
    display_name: Basic Worker
    system_prompt: "You are a helpful worker agent."

  specialist:
    type: native
    model: test
    display_name: Domain Specialist
    system_prompt: "You are a specialist with deep domain knowledge."
"""

WORKERS_WITH_SHARING = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - name: worker
        type: agent
        pass_message_history: true
      - specialist

  worker:
    type: native
    model: test
    display_name: History Worker
    system_prompt: "You are a worker with conversation history."

  specialist:
    type: native
    model: test
    display_name: Context Worker
    system_prompt: "You are a worker with context access."
"""

INVALID_WORKERS = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - nonexistent
"""

STRUCTURED_WORKER = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - structured_worker

  structured_worker:
    model: test
    display_name: Structured Worker
    system_prompt: "You are a worker that returns structured data."
"""


def write_config(content: str, path: Path) -> Path:
    """Write config content to a file."""
    config_file = path / "agents.yml"
    config_file.write_text(content)
    return config_file


def _get_agent(pool: AgentPool, name: str) -> Agent[Any, Any]:  # type: ignore[return-type]
    """Create an agent from pool manifest config."""
    cfg = pool.manifest.agents[name]
    return cast(Agent[Any, Any], cfg.get_agent(pool=pool))


async def test_basic_worker_setup(tmp_path: Path):
    """Test basic worker registration and usage."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        async with main_agent:
            # Verify workers were registered as tools via toolset
            tools = await main_agent.tools.get_tools()
            tool_names = [t.name for t in tools]
            assert "ask_worker" in tool_names
            assert "ask_specialist" in tool_names


async def test_history_sharing(tmp_path: Path):
    """Test history sharing between agents."""
    config_path = write_config(WORKERS_WITH_SHARING, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        # Configure models: TestModel for both agents
        main_model = TestModel(call_tools=["ask_worker"])
        worker_model = TestModel(custom_output_text="The value is 42")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)
            # Create some conversation history
            await main_agent.run("Remember X equals 42")
            # Worker should have access to history
            result = await main_agent.run("Ask worker: What is X?")
            assert "42" in result.content


async def test_worker_context_sharing(tmp_path: Path):
    """Test context sharing between agents."""
    config_path = write_config(WORKERS_WITH_SHARING, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        specialist = _get_agent(pool, "specialist")
        assert isinstance(main_agent, Agent)
        assert isinstance(specialist, Agent)
        async with main_agent, specialist:
            main_model = TestModel(call_tools=["ask_specialist"])
            specialist_model = TestModel(custom_output_text="I can see context value: 123")
            await main_agent.set_model(main_model)
            await specialist.set_model(specialist_model)
            prompt = "Ask specialist: What's in the context?"
            result = await main_agent.run(prompt, deps={"important_value": 123})
            assert "123" in result.data


async def test_invalid_worker(tmp_path: Path):
    """Test error when using non-existent worker."""
    config_path = write_config(INVALID_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    # With toolset approach, error happens at tool call time, not pool init
    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        async with main_agent:
            # Tool is created but will fail when called
            tools = await main_agent.tools.get_tools()
            tool_names = [t.name for t in tools]
            assert "ask_nonexistent" in tool_names


async def test_worker_independence(tmp_path: Path):
    """Test that workers maintain independent state when not sharing."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        async with main_agent:
            # Create history in main agent
            await main_agent.run("Remember X equals 42")
            # Worker should not see this history
            result = await main_agent.run("Ask worker: What is X?")
            assert "42" not in result.data


async def test_multiple_workers_same_prompt(tmp_path: Path):
    """Test using multiple workers with the same prompt."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        specialist = _get_agent(pool, "specialist")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        assert isinstance(specialist, Agent)
        async with main_agent, worker, specialist:
            main_model = TestModel(call_tools=["ask_worker", "ask_specialist"])
            worker_model = TestModel(custom_output_text="I am a helpful worker assistant")
            specialist_model = TestModel(custom_output_text="I am a domain specialist")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)
            await specialist.set_model(specialist_model)
            responses = []
            main_agent.message_sent.connect(lambda msg: responses.append(msg.content))
            await main_agent.run("Ask both workers: introduce yourselves")
            assert len(responses) > 0
            assert any("helpful worker" in r.lower() for r in responses)


async def test_structured_worker_output(default_model: str):
    """Test that agents with BaseModel output convert correctly when used as tools."""
    # Create structured agent and main agent that will use him as a tool
    structured = Agent(name="structured_agent", model=default_model, output_type=StructuredResponse)
    main_agent = Agent(name="main_agent", model=default_model)
    # Convert structured agent to tool and register with main agent
    tool = structured.to_tool()
    # Verify that return type annotation is set correctly
    assert tool.callable.__annotations__.get("return") == StructuredResponse
    main_agent.tools.register_tool(tool, enabled=True)
    # Test that both agents work together
    async with structured, main_agent:
        result = await main_agent.run("Ask structured_agent: return a message 'test' with value 42")
        tool_calls = result.get_tool_calls()
        assert len(tool_calls) > 0
        # Verify pydantic-ai properly converted the result to StructuredResponse
        structured_result = tool_calls[0].result
        assert isinstance(structured_result, StructuredResponse)
        assert structured_result.message
        assert structured_result.value


async def test_worker_emits_spawn_session_start_event(tmp_path: Path):
    """Test that worker tool emits SpawnSessionStart event."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            session_pool = pool.session_pool
            assert session_pool is not None

            # Set up test model to trigger worker tool
            main_model = TestModel(call_tools=["ask_worker"])
            worker_model = TestModel(custom_output_text="Worker result")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            # Collect events through run_stream
            async for event in session_pool.run_stream("ses_test", "Ask worker: do something"):
                if isinstance(event, SpawnSessionStart):
                    events.append(event)

    # Verify SpawnSessionStart was emitted
    assert len(events) == 1
    spawn_event = events[0]
    assert spawn_event.source_name == "worker"
    assert spawn_event.spawn_mechanism == "task"
    assert spawn_event.child_session_id is not None
    assert spawn_event.parent_session_id is not None
    assert spawn_event.child_session_id.startswith("ses_")


async def test_worker_emits_subagent_events(tmp_path: Path):
    """Test that worker tool emits child session events via EventBus descendants scope.

    After the refactoring (commit 2d72eddd4), worker tools no longer wrap child
    session events in SubAgentEvent. Instead, events from child sessions flow
    through the EventBus directly and are received when subscribing with
    ``scope="descendants"``.
    """
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []
    child_events: list[StreamCompleteEvent] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            session_pool = pool.session_pool
            assert session_pool is not None

            main_model = TestModel(call_tools=["ask_worker"])
            worker_model = TestModel(custom_output_text="Worker output")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            # Collect events through run_stream with descendants scope to catch child events
            async for event in session_pool.run_stream(
                "ses_test", "Ask worker: do something", scope="descendants"
            ):
                if isinstance(event, SpawnSessionStart):
                    spawn_events.append(event)
                elif isinstance(event, StreamCompleteEvent) and event.session_id != "ses_test":
                    child_events.append(event)

    # Verify SpawnSessionStart was emitted
    assert len(spawn_events) == 1
    assert spawn_events[0].source_name == "worker"
    assert spawn_events[0].child_session_id is not None
    assert spawn_events[0].child_session_id.startswith("ses_")

    # Verify child session events came through the EventBus directly.
    child_complete = [
        e for e in child_events
        if e.session_id != "ses_test"
    ]
    assert len(child_complete) >= 1, (
        f"Expected at least 1 child StreamCompleteEvent, got {len(child_complete)}"
    )
    assert child_complete[0].message.content == "Worker output"


async def test_worker_session_isolation(tmp_path: Path):
    """Test that worker runs have isolated session IDs."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            session_pool = pool.session_pool
            assert session_pool is not None

            # Set up test model to call worker twice
            main_model = TestModel(call_tools=["ask_worker", "ask_worker"])
            worker_model = TestModel(custom_output_text="Result")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            # Collect events through run_stream
            async for event in session_pool.run_stream("ses_test", "Ask worker twice"):
                if isinstance(event, SpawnSessionStart):
                    spawn_events.append(event)

    # Verify each worker run got a unique session ID
    assert len(spawn_events) == 2
    session_ids = [e.child_session_id for e in spawn_events]
    assert session_ids[0] != session_ids[1], "Each worker run should have unique session ID"

    # Verify parent session is consistent
    parent_ids = [e.parent_session_id for e in spawn_events]
    assert parent_ids[0] == parent_ids[1], "All worker runs should share same parent session"


async def test_worker_team_emits_events(tmp_path: Path):
    """Test that team workers also emit proper events."""
    TEAM_CONFIG = """\
agents:
  main:
    type: native
    model: test
    display_name: Main Agent
    workers:
      - my_team

  agent1:
    type: native
    model: test
    display_name: Agent 1
    system_prompt: "You are agent 1."

  agent2:
    type: native
    model: test
    display_name: Agent 2
    system_prompt: "You are agent 2."

teams:
  my_team:
    mode: parallel
    members: [agent1, agent2]
"""
    config_path = write_config(TEAM_CONFIG, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        assert isinstance(main_agent, Agent)
        async with main_agent:
            session_pool = pool.session_pool
            assert session_pool is not None

            main_model = TestModel(call_tools=["ask_my_team"])
            await main_agent.set_model(main_model)

            # Collect events through run_stream
            async for event in session_pool.run_stream("ses_test", "Ask team to do something"):
                if isinstance(event, SpawnSessionStart):
                    spawn_events.append(event)

    # Verify SpawnSessionStart was emitted for team
    assert len(spawn_events) == 1
    assert spawn_events[0].source_name == "my_team"
    assert spawn_events[0].source_type == "team_parallel"


async def test_worker_spawn_depth_equals_parent_depth_plus_one(tmp_path: Path):
    """Test that worker spawn depth equals parent depth + 1."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            session_pool = pool.session_pool
            assert session_pool is not None

            # Set up test model to trigger worker tool at depth 0 (top-level)
            main_model = TestModel(call_tools=["ask_worker"])
            worker_model = TestModel(custom_output_text="Worker result")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            # Collect SpawnSessionStart events via run_stream
            async for event in session_pool.run_stream("ses_test", "Ask worker: do something"):
                if isinstance(event, SpawnSessionStart):
                    spawn_events.append(event)

    # Verify depth is 1 when parent runs at depth 0
    assert len(spawn_events) == 1
    assert spawn_events[0].depth == 1


async def test_worker_child_session_has_correct_parent(tmp_path: Path):
    """Test that worker child sessions are created with correct parent session."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            session_pool = pool.session_pool
            assert session_pool is not None

            main_model = TestModel(call_tools=["ask_worker"])
            worker_model = TestModel(custom_output_text="Worker result")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            # Collect events through run_stream
            async for event in session_pool.run_stream("ses_test", "Ask worker: do something"):
                if isinstance(event, SpawnSessionStart):
                    spawn_events.append(event)

    assert len(spawn_events) == 1
    spawn = spawn_events[0]
    # Child session ID must be distinct from parent
    assert spawn.child_session_id != spawn.parent_session_id
    # Both session IDs must be valid (start with ses_)
    assert spawn.child_session_id.startswith("ses_")
    assert spawn.parent_session_id.startswith("ses_")


async def test_delegation_depth_error_at_max_depth(tmp_path: Path):
    """Test that DelegationDepthError is raised when max delegation depth is exceeded."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            main_model = TestModel(call_tools=["ask_worker"])
            worker_model = TestModel(custom_output_text="Worker result")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            # Run at max depth — the worker tool should raise DelegationDepthError
            depth_exceeded = False
            try:
                # Run at max depth by providing a pre-configured depth
                async for event in main_agent.run_stream(
                    "Ask worker: do something", depth=MAX_DELEGATION_DEPTH, session_id="ses_test"
                ):
                    if isinstance(event, SpawnSessionStart):
                        pass  # Should not reach here
            except DelegationDepthError:
                depth_exceeded = True

        assert depth_exceeded, "Expected DelegationDepthError when running at max depth"


async def test_subagent_event_depth_propagation(tmp_path: Path):
    """Test that SpawnSessionStart depth is consistent and child events are received.

    After the refactoring (commit 2d72eddd4), SubAgentEvent is no longer emitted.
    Child session events come through the EventBus directly via scope="descendants".
    This test verifies that SpawnSessionStart carries the correct depth and that
    child session events are properly associated with the child session.
    """
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []
    child_complete_events: list[StreamCompleteEvent] = []

    async with AgentPool(manifest) as pool:
        main_agent = _get_agent(pool, "main")
        worker = _get_agent(pool, "worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        async with main_agent, worker:
            session_pool = pool.session_pool
            assert session_pool is not None

            main_model = TestModel(call_tools=["ask_worker"])
            worker_model = TestModel(custom_output_text="Worker result")
            await main_agent.set_model(main_model)
            await worker.set_model(worker_model)

            async for event in session_pool.run_stream(
                "ses_test", "Ask worker: do something", scope="descendants"
            ):
                if isinstance(event, SpawnSessionStart):
                    spawn_events.append(event)
                elif isinstance(event, StreamCompleteEvent) and event.session_id != "ses_test":
                    child_complete_events.append(event)

    # Verify SpawnSessionStart was emitted with correct depth
    assert len(spawn_events) == 1
    expected_depth = spawn_events[0].depth
    assert expected_depth == 1  # Child of root session should have depth 1

    # Verify child session events were received with matching session ID.
    child_complete = [
        e for e in child_complete_events
        if e.session_id != "ses_test"
    ]
    assert len(child_complete) >= 1, (
        f"Expected at least 1 child StreamCompleteEvent, got {len(child_complete)}"
    )
    assert child_complete[0].message.content == "Worker result"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
