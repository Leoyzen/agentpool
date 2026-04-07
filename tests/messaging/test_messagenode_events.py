from typing import Any
from unittest.mock import AsyncMock

from pydantic_ai import TextPartDelta
import pytest

from agentpool.agents.events import PartDeltaEvent
from agentpool.messaging import ChatMessage
from agentpool.messaging.messagenode import MessageNode


class ConcreteMessageNode(MessageNode[Any, Any]):
    async def run(self, *prompts: Any, **kwargs: Any) -> ChatMessage[Any]:
        return ChatMessage(content="test", role="assistant")

    async def get_stats(self):
        pass

    def run_iter(self, *prompts: Any, **kwargs: Any):
        pass


@pytest.mark.asyncio
async def test_messagenode_event_routing():
    node = ConcreteMessageNode(name="test_node")

    # Mock EventManager.emit_agent_event
    node._events.emit_agent_event = AsyncMock()

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="test delta"))
    node.session_id = "test_session"

    await node.emit_agent_event(event)

    node._events.emit_agent_event.assert_awaited_once_with(event, source_session_id="test_session")


@pytest.mark.asyncio
async def test_messagenode_set_session_context():
    node = ConcreteMessageNode(name="test_node")

    node.set_session_context(session_id="s1", parent_session_id="p1")

    assert node.session_id == "s1"
    assert node.parent_session_id == "p1"
    assert node._events.session_id == "s1"
    assert node._events.parent_session_id == "p1"


@pytest.mark.asyncio
async def test_messagenode_init_event_manager():
    node = ConcreteMessageNode(name="test_node")
    assert node._events.session_id is None
    assert node._events.parent_session_id is None
