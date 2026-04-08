from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentPool, AgentsManifest
from agentpool.agents.events import SpawnSessionStart, StreamCompleteEvent, SubAgentEvent


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


async def test_basic_worker_setup(tmp_path: Path):
    """Test basic worker registration and usage."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
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
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        # Configure models: real model for main agent, TestModel for worker
        await main_agent.set_model("openai:gpt-5-nano")
        worker_model = TestModel(custom_output_text="The value is 42")
        assert isinstance(worker, Agent)
        await worker.set_model(worker_model)
        # Create some conversation history
        result = await main_agent.run("Remember X equals 42")
        # Worker should have access to history
        result = await main_agent.run("Ask worker: What is X?")
        assert "42" in result.content


async def test_worker_context_sharing(tmp_path: Path):
    """Test context sharing between agents."""
    config_path = write_config(WORKERS_WITH_SHARING, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main", deps_type=dict)
        specialist = pool.get_agent("specialist")
        assert isinstance(main_agent, Agent)
        assert isinstance(specialist, Agent)
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
        main_agent = pool.get_agent("main")
        # Tool is created but will fail when called
        tools = await main_agent.tools.get_tools()
        tool_names = [t.name for t in tools]
        assert "ask_nonexistent" in tool_names


async def test_worker_independence(tmp_path: Path):
    """Test that workers maintain independent state when not sharing."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)
    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
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
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        specialist = pool.get_agent("specialist")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)
        assert isinstance(specialist, Agent)
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
    """Test that agents with BaseModel output    convert correctly when used as tools."""
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
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)

        # Set up test model to trigger worker tool
        main_model = TestModel(call_tools=["ask_worker"])
        worker_model = TestModel(custom_output_text="Worker result")
        await main_agent.set_model(main_model)
        await worker.set_model(worker_model)

        # Collect events through run_stream
        async for event in main_agent.run_stream("Ask worker: do something"):
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
    """Test that worker tool emits SubAgentEvent wrapping worker events."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    subagent_events: list[SubAgentEvent] = []

    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)

        main_model = TestModel(call_tools=["ask_worker"])
        worker_model = TestModel(custom_output_text="Worker output")
        await main_agent.set_model(main_model)
        await worker.set_model(worker_model)

        # Collect events through run_stream
        async for event in main_agent.run_stream("Ask worker: do something"):
            if isinstance(event, SubAgentEvent):
                subagent_events.append(event)

    # Verify SubAgentEvents were emitted
    assert len(subagent_events) > 0

    # Find the StreamCompleteEvent wrapped in SubAgentEvent
    complete_events = [e for e in subagent_events if isinstance(e.event, StreamCompleteEvent)]
    assert len(complete_events) > 0

    # Verify event has correct session tracking
    for event in subagent_events:
        assert event.child_session_id is not None
        assert event.child_session_id.startswith("ses_")
        assert event.source_name == "worker"


async def test_worker_session_isolation(tmp_path: Path):
    """Test that worker runs have isolated session IDs."""
    config_path = write_config(BASIC_WORKERS, tmp_path)
    manifest = AgentsManifest.from_file(config_path)

    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        main_agent = pool.get_agent("main")
        worker = pool.get_agent("worker")
        assert isinstance(main_agent, Agent)
        assert isinstance(worker, Agent)

        # Set up test model to call worker twice
        main_model = TestModel(call_tools=["ask_worker", "ask_worker"])
        worker_model = TestModel(custom_output_text="Result")
        await main_agent.set_model(main_model)
        await worker.set_model(worker_model)

        # Collect events through run_stream
        async for event in main_agent.run_stream("Ask worker twice"):
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
        main_agent = pool.get_agent("main")
        assert isinstance(main_agent, Agent)

        main_model = TestModel(call_tools=["ask_my_team"])
        await main_agent.set_model(main_model)

        # Collect events through run_stream
        async for event in main_agent.run_stream("Ask team to do something"):
            if isinstance(event, SpawnSessionStart):
                spawn_events.append(event)

    # Verify SpawnSessionStart was emitted for team
    assert len(spawn_events) == 1
    assert spawn_events[0].source_name == "my_team"
    assert spawn_events[0].source_type == "team_parallel"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
