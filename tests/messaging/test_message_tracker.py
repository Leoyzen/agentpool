from __future__ import annotations

from dataclasses import replace

import pytest

from agentpool import Agent, AgentPool, Team
from agentpool.messaging import ChatMessage
from agentpool.models.agents import NativeAgentConfig
from agentpool.models.manifest import AgentsManifest


def _make_pool() -> AgentPool:
    """Create a pool with a single agent in the manifest."""
    manifest = AgentsManifest(
        agents={"agent1": NativeAgentConfig(name="agent1", model="test")}
    )
    return AgentPool(manifest)


def _forwarded(msg: ChatMessage[Any], agent_name: str) -> ChatMessage[Any]:
    """Create a forwarded copy of *msg* with a different sender name.

    The session_id is preserved so that MessageFlowTracker.visualize()
    can correlate the event with the original conversation.
    """
    return replace(msg, name=agent_name)


async def test_simple_sequential_chain():
    """Test basic sequential chaining."""
    async with _make_pool() as pool:
        agent1 = Agent("agent1", model="test", agent_pool=pool)
        agent2 = Agent("agent2", model="test", agent_pool=pool)
        agent3 = Agent("agent3", model="test")
        agent1 >> agent2 >> agent3
        async with pool.track_message_flow() as tracker:
            msg = await agent1.run("test")
            # Manually route through agent2's connections so that
            # connection_processed fires with a consistent session_id.
            # (agent.run() no longer auto-forwards through >> chains;
            # downstream agents produce messages with different session_ids.)
            await agent2.connections.route_message(_forwarded(msg, "agent2"))
            mermaid = tracker.visualize(msg)
            # Should only see these two connections
            connections = mermaid.replace(" ", "").split("\n")[1:]  # pyright: ignore
            assert sorted(connections) == sorted(["agent1-->agent2", "agent2-->agent3"])


async def test_parallel_to_sequential():
    """Test parallel flows connecting to single target."""
    async with _make_pool() as pool:
        agent1 = Agent("agent1", model="test", agent_pool=pool)
        agent2 = Agent("agent2", model="test", agent_pool=pool)
        agent3 = Agent("agent3", model="test", agent_pool=pool)
        agent4 = Agent("agent4", model="test")
        agent1 >> [agent2, agent3] >> agent4
        async with pool.track_message_flow() as tracker:
            msg = await agent1.run("test")
            # Manually route through agent2 and agent3 connections so that
            # connection_processed fires with a consistent session_id.
            await agent2.connections.route_message(_forwarded(msg, "agent2"))
            await agent3.connections.route_message(_forwarded(msg, "agent3"))
            mermaid = tracker.visualize(msg)
            connections = mermaid.replace(" ", "").split("\n")[1:]  # pyright: ignore
            assert sorted(connections) == sorted([
                "agent1-->agent2",
                "agent1-->agent3",
                "agent2-->agent4",
                "agent3-->agent4",
            ])


@pytest.mark.skip(reason="Flaky: fails due to cross-test state pollution in batch runs")
async def test_callback_chain():
    """Test chaining with a callback function."""
    async with _make_pool() as pool:
        agent1 = Agent("agent1", model="test", agent_pool=pool)
        agent2 = Agent("agent2", model="test")

        def process(msg: str) -> str:
            return f"Processed: {msg}"

        _talk = agent1 >> process >> agent2
        async with pool.track_message_flow() as tracker:
            msg = await agent1.run("test")
            mermaid = tracker.visualize(msg)
            connections = mermaid.replace(" ", "").split("\n")[1:]  # pyright: ignore
            assert sorted(connections) == sorted(["agent1-->process", "process-->agent2"])


async def test_message_flow_tracker():
    """Test tracking and visualizing message flow through a chain."""
    # Setup a simple agent chain
    async with _make_pool() as pool:
        agent1 = Agent("agent1", system_prompt="You are agent 1", model="test", agent_pool=pool)
        agent2 = Agent("agent2", system_prompt="You are agent 2", model="test", agent_pool=pool)
        agent3 = Agent("agent3", system_prompt="You are agent 3", model="test")

        # Create chain: agent1 >> agent2 >> agent3
        agent1 >> agent2
        agent2 >> agent3

        # Track message flow during execution
        async with pool.track_message_flow() as tracker:
            result = await agent1.run("Hello")
            # Manually route through agent2's connections so that
            # connection_processed fires with a consistent session_id.
            await agent2.connections.route_message(_forwarded(result, "agent2"))

            # Get flow visualization
            mermaid = tracker.visualize(result)

            # Check for expected connections in diagram
            assert "flowchart LR" in mermaid
            assert "agent1-->agent2" in mermaid.replace(" ", "")
            assert "agent2-->agent3" in mermaid.replace(" ", "")

            # Should not contain non-existent connections
            assert "agent1-->agent3" not in mermaid.replace(" ", "")
            assert "agent3-->agent1" not in mermaid.replace(" ", "")

        # Tracker should no longer receive events after context exit
        assert len(tracker.events) > 0  # Should have events from the run
        previous_count = len(tracker.events)

        # Run again outside context
        await agent1.run("Another message")
        assert len(tracker.events) == previous_count  # No new events tracked


async def test_message_flow_tracker_parallel():
    """Test tracking parallel message flows."""
    async with _make_pool() as pool:
        agent1 = Agent("agent1", model="test", agent_pool=pool)
        agent2 = Agent("agent2", model="test")
        agent3 = Agent("agent3", model="test")

        # Create parallel flows: agent1 >> [agent2, agent3]
        agent1 >> [agent2, agent3]

        async with pool.track_message_flow() as tracker:
            result = await agent1.run("Hello")
            mermaid = tracker.visualize(result)

            # Both parallel paths should be in diagram
            assert "agent1-->agent2" in mermaid.replace(" ", "")
            assert "agent1-->agent3" in mermaid.replace(" ", "")

            # With lazy session_id init, consecutive runs share the same conversation
            # so subsequent visualizations will include all events for that conversation
            other_result = await agent1.run("Different conversation")
            other_mermaid = tracker.visualize(other_result)

            # Both runs share the same session_id, so other_mermaid includes all events
            assert "agent1-->agent2" in other_mermaid.replace(" ", "")
            assert "agent1-->agent3" in other_mermaid.replace(" ", "")


async def test_message_flow_tracker_nested():
    """Test tracking flow through nested teams."""
    async with _make_pool() as pool:
        agent1 = Agent("agent1", model="test", agent_pool=pool)
        agent2 = Agent("agent2", model="test", agent_pool=pool)
        agent3 = Agent("agent3", model="test")

        # Create nested team using Team constructor instead of pool.create_team()
        team = Team([agent2, agent3], name="team")
        agent1 >> team

        async with pool.track_message_flow() as tracker:
            result = await agent1.run("Hello")
            mermaid = tracker.visualize(result)

            # Should only show connection to team as a unit
            connections = mermaid.replace(" ", "").split("\n")[1:]  # pyright: ignore
            assert sorted(connections) == ["agent1-->team"]


if __name__ == "__main__":
    pytest.main([__file__, "-vv"])
