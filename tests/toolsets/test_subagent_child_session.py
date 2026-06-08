"""Tests for SubagentTools child session creation, spawn dedup, and depth guard.

Verifies RFC-0028 Task T9 requirements:
- Exactly one SpawnSessionStart emitted per delegation from task()
- SpawnSessionStart is emitted from task(), NOT from _stream_task()
- ctx.run_ctx.depth is used instead of getattr(ctx, "current_depth", 0)
- MAX_DELEGATION_DEPTH guard is enforced before child session creation
- session_id, parent_session_id, and depth are passed into child run_stream()
- No identifier.ascending("session") for provider-owned child IDs
- Child SessionData persists with correct parent_id
- RunStartedEvent.session_id matches SpawnSessionStart.child_session_id
- DelegationDepthError raised at max depth
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool import AgentPool, AgentsManifest
from agentpool.agents.context import AgentContext, AgentRunContext
from agentpool.agents.events import (
    RunStartedEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
)
from agentpool.agents.exceptions import MAX_DELEGATION_DEPTH, DelegationDepthError
from agentpool.sessions import SessionData
from agentpool.sessions.store import MemorySessionStore
from agentpool_toolsets.builtin.subagent_tools import SubagentTools, _stream_task


# ---------------------------------------------------------------------------
# Single SpawnSessionStart per delegation (integration-level)
# ---------------------------------------------------------------------------


async def test_single_spawn_session_start_per_delegation() -> None:
    """task() emits exactly one SpawnSessionStart — not duplicated by _stream_task()."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: You are a worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Do work"
          description: "Test spawn"
    tools:
      - type: subagent
""")
    spawn_count = 0

    async with AgentPool(manifest) as pool:
        orchestrator = pool.get_agent("orchestrator")

        async for event in orchestrator.run_stream("Delegate", session_id="ses_test"):
            if isinstance(event, SpawnSessionStart):
                spawn_count += 1

    assert spawn_count == 1, (
        f"Expected exactly 1 SpawnSessionStart, got {spawn_count}. "
        "The event should be emitted once from task(), not duplicated in _stream_task()."
    )


# ---------------------------------------------------------------------------
# RunStartedEvent.session_id == SpawnSessionStart.child_session_id
# ---------------------------------------------------------------------------


async def test_run_started_session_id_matches_spawn_child_id() -> None:
    """RunStartedEvent from child agent carries same session_id as SpawnSessionStart.child_session_id."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Child done"
    system_prompt: You are a worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Session match test"
    tools:
      - type: subagent
""")
    child_session_id_from_spawn: str | None = None
    child_session_ids_from_run_started: list[str] = []

    async with AgentPool(manifest) as pool:
        orchestrator = pool.get_agent("orchestrator")

        async for event in orchestrator.run_stream("Delegate", session_id="ses_test"):
            if isinstance(event, SpawnSessionStart):
                child_session_id_from_spawn = event.child_session_id
            elif isinstance(event, SubAgentEvent) and isinstance(event.event, RunStartedEvent):
                child_session_ids_from_run_started.append(event.event.session_id)

    assert child_session_id_from_spawn is not None, "SpawnSessionStart was not emitted"
    assert child_session_ids_from_run_started, "No RunStartedEvent found in SubAgentEvents"
    assert child_session_id_from_spawn in child_session_ids_from_run_started, (
        f"RunStartedEvent.session_id {child_session_ids_from_run_started} "
        f"should contain SpawnSessionStart.child_session_id {child_session_id_from_spawn}"
    )


# ---------------------------------------------------------------------------
# Child SessionData persists with correct parent_id
# ---------------------------------------------------------------------------


async def test_child_session_data_persists_with_parent_id() -> None:
    """Child session created by task() is persisted with correct parent_id."""
    store = MemorySessionStore()
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: Worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Persist test"
    tools:
      - type: subagent
""")

    async with AgentPool(manifest) as pool:
        # Swap in our observable store
        assert pool.session_pool is not None
        pool.session_pool.store = store

        orch = pool.get_agent("orchestrator")

        child_session_id_from_spawn: str | None = None

        async for event in orch.run_stream("Delegate", session_id="ses_test"):
            if isinstance(event, SpawnSessionStart):
                child_session_id_from_spawn = event.child_session_id

        assert child_session_id_from_spawn is not None, "SpawnSessionStart not emitted"
        parent_session_id = orch.session_id
        assert parent_session_id is not None

    # Verify child session was persisted
    child_data = await store.load(child_session_id_from_spawn)
    assert child_data is not None, (
        f"Child session {child_session_id_from_spawn} was not persisted in store"
    )
    assert child_data.parent_id == parent_session_id, (
        f"Child parent_id={child_data.parent_id}, expected={parent_session_id}"
    )
    assert child_data.agent_name == "worker"


# ---------------------------------------------------------------------------
# DelegationDepthError at max depth
# ---------------------------------------------------------------------------


async def test_delegation_depth_error_at_max_depth() -> None:
    """DelegationDepthError is raised when current depth >= MAX_DELEGATION_DEPTH."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: Worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Depth test"
    tools:
      - type: subagent
""")
    async with AgentPool(manifest) as pool:
        orch = pool.get_agent("orchestrator")

        tools_provider = SubagentTools()

        ctx = AgentContext(node=orch)
        ctx.pool = pool
        ctx.run_ctx = AgentRunContext(depth=MAX_DELEGATION_DEPTH)

        with pytest.raises(DelegationDepthError) as exc_info:
            await tools_provider.task(
                ctx=ctx,
                agent_or_team="worker",
                prompt="Should fail",
                description="Depth overflow",
            )

        assert exc_info.value.current_depth == MAX_DELEGATION_DEPTH + 1


# ---------------------------------------------------------------------------
# _stream_task does NOT emit SpawnSessionStart
# ---------------------------------------------------------------------------


async def test_stream_task_does_not_emit_spawn_session_start() -> None:
    """_stream_task() does not emit SpawnSessionStart — only wraps events as SubAgentEvent."""
    mock_ctx = MagicMock(spec=AgentContext)
    mock_ctx.events = MagicMock()
    mock_ctx.events.emit_event = AsyncMock()

    final_msg = MagicMock()
    final_msg.content = "Test result"

    async def fake_stream() -> AsyncIterator[StreamCompleteEvent[Any]]:
        yield StreamCompleteEvent(message=final_msg)

    result = await _stream_task(
        mock_ctx,
        source_name="test_agent",
        source_type="agent",
        stream=fake_stream(),
        child_session_id="child_ses_123",
        parent_session_id="parent_ses_456",
    )

    # Verify SpawnSessionStart was NOT emitted by _stream_task
    for call in mock_ctx.events.emit_event.call_args_list:
        event = call.args[0]
        assert not isinstance(event, SpawnSessionStart), (
            "_stream_task() should not emit SpawnSessionStart — "
            "it should be emitted only by task()"
        )

    # Verify result contains the session_id
    assert result["metadata"]["sessionId"] == "child_ses_123"

    # Verify SubAgentEvent was emitted
    assert mock_ctx.events.emit_event.call_count >= 1


# ---------------------------------------------------------------------------
# Depth guard enforced BEFORE child session creation
# ---------------------------------------------------------------------------


async def test_depth_guard_before_session_creation() -> None:
    """When depth >= MAX_DELEGATION_DEPTH, no child session is created."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: Worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Depth guard test"
    tools:
      - type: subagent
""")
    async with AgentPool(manifest) as pool:
        orch = pool.get_agent("orchestrator")

        tools_provider = SubagentTools()

        ctx = AgentContext(node=orch)
        ctx.pool = pool
        ctx.run_ctx = AgentRunContext(depth=MAX_DELEGATION_DEPTH)

        # create_child_session should NOT be called because depth guard fires first
        with (
            patch.object(ctx, "create_child_session", new_callable=AsyncMock) as mock_create,
            pytest.raises(DelegationDepthError),
        ):
            await tools_provider.task(
                ctx=ctx,
                agent_or_team="worker",
                prompt="Should fail before session creation",
                description="Depth guard",
            )

        mock_create.assert_not_called()


# ---------------------------------------------------------------------------
# Depth uses ctx.run_ctx.depth, not getattr
# ---------------------------------------------------------------------------


async def test_task_uses_run_ctx_depth() -> None:
    """task() uses ctx.run_ctx.depth for computing delegation depth."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: Worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Depth source test"
    tools:
      - type: subagent
""")
    spawn_depth: int | None = None

    async with AgentPool(manifest) as pool:
        orch = pool.get_agent("orchestrator")

        # With depth=0 (default top-level), child should be depth=1
        async for event in orch.run_stream("Delegate", session_id="ses_test"):
            if isinstance(event, SpawnSessionStart):
                spawn_depth = event.depth

    assert spawn_depth == 1, f"Expected depth=1 for first delegation, got {spawn_depth}"


# ---------------------------------------------------------------------------
# No identifier.ascending("session") for child IDs in subagent_tools.py
# ---------------------------------------------------------------------------


async def test_subagent_tools_does_not_import_identifiers() -> None:
    """subagent_tools module does not import identifier — uses create_child_session instead."""
    import agentpool_toolsets.builtin.subagent_tools as mod

    # The module should not have 'identifier' in its namespace
    assert not hasattr(mod, "identifier"), (
        "subagent_tools should not import 'identifier' — it should use ctx.create_child_session()"
    )


# ---------------------------------------------------------------------------
# run_mode field on SpawnSessionStart (RFC-0028 T3)
# ---------------------------------------------------------------------------


async def test_task_sync_mode_sets_run_mode_foreground() -> None:
    """task() with async_mode=False sets run_mode='foreground' on SpawnSessionStart."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: Worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Sync run_mode test"
    tools:
      - type: subagent
""")
    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        orch = pool.get_agent("orchestrator")
        async for event in orch.run_stream("Delegate"):
            if isinstance(event, SpawnSessionStart):
                spawn_events.append(event)

    assert len(spawn_events) == 1
    assert spawn_events[0].run_mode == "foreground"


async def test_task_async_mode_sets_run_mode_background() -> None:
    """task() with async_mode=True sets run_mode='background' on SpawnSessionStart."""
    manifest = AgentsManifest.from_yaml("""
agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: Worker.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Work"
          description: "Async run_mode test"
          async_mode: true
    tools:
      - type: subagent
""")
    spawn_events: list[SpawnSessionStart] = []

    async with AgentPool(manifest) as pool:
        orch = pool.get_agent("orchestrator")
        async for event in orch.run_stream("Delegate"):
            if isinstance(event, SpawnSessionStart):
                spawn_events.append(event)

    assert len(spawn_events) == 1
    assert spawn_events[0].run_mode == "background"
