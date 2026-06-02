"""Tests for EventBus descendant scope propagation.

Verifies that subscribers with scope="descendants" receive events from
their own session as well as child sessions.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import EventBus


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ---------------------------------------------------------------------------
# Descendant scope
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_descendant_scope_receives_child_event() -> None:
    """A subscriber with scope='descendants' on parent receives child events."""
    event_bus = EventBus(max_queue_size=10)
    parent_id = "parent-session"
    child_id = f"{parent_id}/child"

    # Set up session hierarchy in the event bus tree
    event_bus._session_tree[parent_id] = [child_id]

    # Subscribe to parent with descendant scope
    queue = await event_bus.subscribe(parent_id, scope="descendants")

    # Publish an event from the child session
    event = RunStartedEvent(session_id=child_id, run_id="run-child-1")
    await event_bus.publish(child_id, event)

    # Subscriber should receive the event
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-child-1"


@pytest.mark.anyio
async def test_descendant_scope_receives_own_event() -> None:
    """A subscriber with scope='descendants' also receives its own session events."""
    event_bus = EventBus(max_queue_size=10)
    parent_id = "parent-session"
    child_id = f"{parent_id}/child"

    event_bus._session_tree[parent_id] = [child_id]

    queue = await event_bus.subscribe(parent_id, scope="descendants")

    # Publish from the parent session itself
    event = RunStartedEvent(session_id=parent_id, run_id="run-parent-1")
    await event_bus.publish(parent_id, event)

    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-parent-1"


@pytest.mark.anyio
async def test_descendant_scope_does_not_receive_unrelated_event() -> None:
    """A subscriber with scope='descendants' does not receive unrelated session events."""
    event_bus = EventBus(max_queue_size=10)
    parent_id = "parent-session"
    child_id = f"{parent_id}/child"
    unrelated_id = "other-session"

    event_bus._session_tree[parent_id] = [child_id]

    queue = await event_bus.subscribe(parent_id, scope="descendants")

    # Publish from an unrelated session
    event = RunStartedEvent(session_id=unrelated_id, run_id="run-other-1")
    await event_bus.publish(unrelated_id, event)

    # Queue should remain empty
    assert queue.empty()


@pytest.mark.anyio
async def test_descendant_scope_receives_grandchild_event() -> None:
    """A subscriber with scope='descendants' receives events from grandchildren."""
    event_bus = EventBus(max_queue_size=10)
    parent_id = "parent-session"
    child_id = f"{parent_id}/child"
    grandchild_id = f"{child_id}/grandchild"

    # Set up nested hierarchy
    event_bus._session_tree[parent_id] = [child_id]
    event_bus._session_tree[child_id] = [grandchild_id]

    queue = await event_bus.subscribe(parent_id, scope="descendants")

    # Publish from grandchild
    event = RunStartedEvent(session_id=grandchild_id, run_id="run-grandchild-1")
    await event_bus.publish(grandchild_id, event)

    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-grandchild-1"


@pytest.mark.anyio
async def test_descendant_scope_child_does_not_receive_parent() -> None:
    """A child subscriber with scope='descendants' does not receive parent events."""
    event_bus = EventBus(max_queue_size=10)
    parent_id = "parent-session"
    child_id = f"{parent_id}/child"

    event_bus._session_tree[parent_id] = [child_id]

    # Subscribe on child with descendant scope
    queue = await event_bus.subscribe(child_id, scope="descendants")

    # Publish from parent
    event = RunStartedEvent(session_id=parent_id, run_id="run-parent-1")
    await event_bus.publish(parent_id, event)

    # Child should not receive parent events
    assert queue.empty()


@pytest.mark.anyio
async def test_descendant_scope_with_session_controller() -> None:
    """Descendant scope works when using a SessionController for hierarchy queries."""
    from agentpool import AgentPool, AgentsManifest, NativeAgentConfig

    manifest = AgentsManifest(
        agents={"agent1": NativeAgentConfig(name="agent1", model="test")}
    )
    async with AgentPool(manifest) as pool:
        from agentpool.orchestrator.core import SessionController

        controller = SessionController(pool)
        event_bus = EventBus(max_queue_size=10, session_controller=controller)

        parent_id = "parent-session"
        child_id = f"{parent_id}/child"

        # Create sessions through the controller to establish hierarchy
        await controller.get_or_create_session(parent_id, agent_name="agent1")
        await controller.get_or_create_session(
            child_id, agent_name="agent1", parent_session_id=parent_id
        )

        queue = await event_bus.subscribe(parent_id, scope="descendants")

        # Publish from child
        event = RunStartedEvent(session_id=child_id, run_id="run-child-1")
        await event_bus.publish(child_id, event)

        received = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert received is not None
        assert isinstance(received, RunStartedEvent)
        assert received.run_id == "run-child-1"
