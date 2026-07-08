"""Phase 3 integration tests for ACPAgent + Conductor.

Tests backward compatibility (zero-proxy), proxy chain wiring, and
multi-turn hook firing through the Conductor integration path.

All tests use mocks — no real subprocess is spawned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp import InitializeRequest
from acp.schema import (
    AgentMessageChunk,
    TextContentBlock,
    TurnCompleteUpdate,
)
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.acp_agent.turn import ACPTurn
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    PartDeltaEvent,
    StreamCompleteEvent,
)
from agentpool.hooks import AgentHooks, CallableHook, HookResult


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Mock ACP client (reused pattern from test_turn_integration.py)
# ---------------------------------------------------------------------------


class MockACPClient:
    """Mock ACP client implementing ACPClientProtocol for testing."""

    def __init__(
        self,
        *,
        updates: list[Any] | None = None,
        messages: list[Any] | None = None,
        prompt_error: Exception | None = None,
    ) -> None:
        self._updates = updates or []
        self._messages = messages or []
        self._prompt_error = prompt_error
        self.prompt_calls: list[tuple[str, list[Any]]] = []
        self._stop_reason: str | None = "end_turn"

    async def prompt(self, session_id: str, content: list[Any]) -> None:
        self.prompt_calls.append((session_id, content))
        if self._prompt_error:
            raise self._prompt_error

    async def stream_events(self) -> AsyncIterator[Any]:
        for update in self._updates:
            yield update

    @property
    def stop_reason(self) -> str | None:
        return self._stop_reason

    async def get_messages(self, session_id: str) -> list[Any]:
        return list(self._messages)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_update(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(content=TextContentBlock(text=text))


def _make_run_ctx(session_id: str = "conductor-test-session") -> AgentRunContext:
    return AgentRunContext(session_id=session_id)


def _make_acp_agent(
    *,
    use_conductor: bool = True,
    proxy_chain: list[Any] | None = None,
) -> ACPAgent[None]:
    """Create an ACPAgent without entering its context manager."""
    init_request = MagicMock(spec=InitializeRequest)
    return ACPAgent(
        command="test-cmd",
        args=["--flag"],
        name="test-acp-agent",
        init_request=init_request,
        use_conductor=use_conductor,
        proxy_chain=proxy_chain,
    )


def _inject_mocks(agent: ACPAgent[None]) -> MagicMock:
    """Inject mock _api, _client_handler, _connection, _sdk_session_id.

    Returns the mock API for further assertions.
    """
    mock_api = MagicMock(name="ACPAgentAPI")
    agent._api = mock_api
    mock_handler = MagicMock(name="ACPClientHandler")
    mock_handler.cleanup = AsyncMock()
    agent._client_handler = mock_handler
    agent._sdk_session_id = "acp-session-123"
    mock_connection = MagicMock(name="ClientSideConnection")
    mock_connection.close = AsyncMock()
    agent._connection = mock_connection
    agent._init_response = MagicMock(name="InitResponse")
    return mock_api


# ---------------------------------------------------------------------------
# Hook recording helpers
# ---------------------------------------------------------------------------


_hook_calls: list[str] = []


def _reset_hook_calls() -> None:
    _hook_calls.clear()


def _make_recording_hook(event: str) -> CallableHook:
    def _fn(**kwargs: Any) -> HookResult:
        _hook_calls.append(event)
        return {"decision": "allow"}

    return CallableHook(event=event, fn=_fn)  # type: ignore[arg-type]


def _make_turn_with_client(
    client: MockACPClient,
    *,
    hooks: AgentHooks | None = None,
    prompts: list[str] | None = None,
    session_id: str = "conductor-test-session",
) -> ACPTurn:
    """Create an ACPTurn directly with a mock client."""
    return ACPTurn(
        acp_client=client,  # type: ignore[arg-type]
        prompts=prompts or ["test prompt"],
        run_ctx=_make_run_ctx(session_id),
        message_history=[],
        session_id=session_id,
        agent_name="test-acp-agent",
        hooks=hooks,
    )


# ---------------------------------------------------------------------------
# Test 1: use_conductor=True creates Conductor
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_acp_agent_use_conductor_true_creates_conductor() -> None:
    """Given use_conductor=True, __aenter__ calls _setup_conductor().

    We patch _setup_conductor to avoid real subprocess, and verify
    it was called and _conductor is set afterward.
    """
    agent = _make_acp_agent(use_conductor=True)
    _inject_mocks(agent)

    # Patch _start_process and _initialize + _create_session to avoid subprocess
    with (
        patch.object(
            ACPAgent, "_start_process", new_callable=AsyncMock,
        ) as mock_start,
        patch.object(
            ACPAgent, "_initialize", new_callable=AsyncMock,
        ),
        patch.object(
            ACPAgent, "_create_session", new_callable=AsyncMock,
        ),
        patch.object(
            ACPAgent, "_setup_conductor", new_callable=AsyncMock,
        ) as mock_setup_conductor,
        patch("agentpool.agents.acp_agent.acp_agent.run_with_process_monitor"),
        patch("anyio.sleep", new_callable=AsyncMock),
    ):
        mock_start.return_value = MagicMock()
        await agent.__aenter__()

    assert mock_setup_conductor.call_count == 1
    await agent.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Test 2: use_conductor=False → _conductor stays None
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_acp_agent_use_conductor_false_no_conductor() -> None:
    """Given use_conductor=False, __aenter__ skips _setup_conductor().

    _conductor should remain None after initialization.
    """
    agent = _make_acp_agent(use_conductor=False)
    _inject_mocks(agent)

    with (
        patch.object(
            ACPAgent, "_start_process", new_callable=AsyncMock,
        ) as mock_start,
        patch.object(
            ACPAgent, "_initialize", new_callable=AsyncMock,
        ),
        patch.object(
            ACPAgent, "_create_session", new_callable=AsyncMock,
        ),
        patch.object(
            ACPAgent, "_setup_conductor", new_callable=AsyncMock,
        ) as mock_setup_conductor,
        patch("agentpool.agents.acp_agent.acp_agent.run_with_process_monitor"),
        patch("anyio.sleep", new_callable=AsyncMock),
    ):
        mock_start.return_value = MagicMock()
        await agent.__aenter__()

    assert mock_setup_conductor.call_count == 0
    assert agent._conductor is None
    await agent.__aexit__(None, None, None)


# ---------------------------------------------------------------------------
# Test 3: Zero-proxy backward compat (proxy_chain=None)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_acp_agent_zero_proxy_backward_compat() -> None:
    """Given proxy_chain=None, ACPAgent still creates turns and runs.

    The agent should function identically to pre-Conductor behavior.
    """
    agent = _make_acp_agent(use_conductor=True, proxy_chain=None)
    _inject_mocks(agent)

    # Verify create_turn works without proxy_chain
    turn = agent.create_turn(
        prompts=["hello"],
        run_ctx=_make_run_ctx(),
        message_history=[],
    )
    assert isinstance(turn, ACPTurn)
    assert turn._prompts == ["hello"]

    # Verify _setup_conductor passes empty proxy_chain to Conductor
    with (
        patch("acp.conductor.Conductor") as mock_conductor_cls,
    ):
        mock_conductor = AsyncMock()
        mock_conductor.__aenter__ = AsyncMock(return_value=mock_conductor)
        mock_conductor.__aexit__ = AsyncMock(return_value=None)
        mock_conductor_cls.return_value = mock_conductor

        await agent._setup_conductor()

        # Conductor should be called with proxy_chain=[] (empty list)
        call_kwargs = mock_conductor_cls.call_args
        assert call_kwargs.kwargs["proxy_chain"] == []
        assert agent._conductor is mock_conductor


# ---------------------------------------------------------------------------
# Test 4: proxy_chain config is passed to Conductor
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_acp_agent_proxy_chain_config() -> None:
    """Given a proxy_chain list, ACPAgent passes it to Conductor."""
    fake_proxy_1 = MagicMock(name="proxy1")
    fake_proxy_2 = MagicMock(name="proxy2")
    proxy_chain = [fake_proxy_1, fake_proxy_2]

    agent = _make_acp_agent(use_conductor=True, proxy_chain=proxy_chain)
    _inject_mocks(agent)

    with patch("acp.conductor.Conductor") as mock_conductor_cls:
        mock_conductor = AsyncMock()
        mock_conductor.__aenter__ = AsyncMock(return_value=mock_conductor)
        mock_conductor.__aexit__ = AsyncMock(return_value=None)
        mock_conductor_cls.return_value = mock_conductor

        await agent._setup_conductor()

        call_kwargs = mock_conductor_cls.call_args
        assert call_kwargs.kwargs["proxy_chain"] == proxy_chain
        assert len(call_kwargs.kwargs["proxy_chain"]) == 2
        assert agent._conductor is mock_conductor


# ---------------------------------------------------------------------------
# Test 5: Multi-turn hooks fire per turn (not double-fired)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_multi_turn_hooks_fire_per_turn() -> None:
    """Given 3 sequential turns, hooks fire exactly 3 times (once per turn).

    The double-fire guard (hooks_fired set) prevents duplicate firing
    within a single turn. Across turns, each turn gets fresh hooks_fired.
    """
    _reset_hook_calls()
    hooks = AgentHooks(
        pre_turn=[_make_recording_hook("pre_turn")],
        post_turn=[_make_recording_hook("post_turn")],
    )

    for turn_idx in range(3):
        client = MockACPClient(
            updates=[_text_update(f"turn-{turn_idx}"), TurnCompleteUpdate()],
            messages=[_text_update(f"turn-{turn_idx}")],
        )
        turn = _make_turn_with_client(client, hooks=hooks)

        events = [event async for event in turn.execute()]

        # Each turn should produce StreamCompleteEvent
        assert any(isinstance(e, StreamCompleteEvent) for e in events)

    # pre_turn and post_turn should each fire exactly 3 times
    pre_turn_count = _hook_calls.count("pre_turn")
    post_turn_count = _hook_calls.count("post_turn")
    assert pre_turn_count == 3, f"Expected 3 pre_turn calls, got {pre_turn_count}"
    assert post_turn_count == 3, f"Expected 3 post_turn calls, got {post_turn_count}"


# ---------------------------------------------------------------------------
# Test 6: Multi-turn events stream correctly across turns
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_multi_turn_events_stream_correctly() -> None:
    """Given 3 turns, events stream without loss across turns.

    Each turn yields PartDeltaEvent + StreamCompleteEvent. No events
    from one turn should bleed into another.
    """
    _reset_hook_calls()

    for turn_idx in range(3):
        updates = [
            _text_update(f"chunk-{turn_idx}-a"),
            _text_update(f"chunk-{turn_idx}-b"),
            TurnCompleteUpdate(),
        ]
        messages = [_text_update(f"result-{turn_idx}")]
        client = MockACPClient(updates=updates, messages=messages)
        turn = _make_turn_with_client(client)

        events = [event async for event in turn.execute()]

        # Should have 2 PartDeltaEvents + 1 StreamCompleteEvent
        delta_events = [e for e in events if isinstance(e, PartDeltaEvent)]
        complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]

        assert len(delta_events) == 2, (
            f"Turn {turn_idx}: expected 2 deltas, got {len(delta_events)}"
        )
        assert len(complete_events) == 1, (
            f"Turn {turn_idx}: expected 1 complete, got {len(complete_events)}"
        )

        # Verify content matches this turn's expected text
        expected_text = f"result-{turn_idx}"
        assert complete_events[0].message.content == expected_text, (
            f"Turn {turn_idx}: expected content '{expected_text}', "
            f"got '{complete_events[0].message.content}'"
        )

        # Verify final_message is populated per turn
        assert turn._final_message is not None
        assert turn._final_message.content == expected_text


# ---------------------------------------------------------------------------
# Test 7: use_conductor=False fallback still produces correct output
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_use_conductor_false_fallback_works() -> None:
    """Given use_conductor=False, ACPAgent.create_turn() still works.

    The agent should produce ACPTurn instances that execute correctly
    without any Conductor involvement.
    """
    agent = _make_acp_agent(use_conductor=False, proxy_chain=None)
    _inject_mocks(agent)

    # Verify no conductor is set
    assert agent._conductor is None
    assert agent._use_conductor is False

    # Create a turn — should work without conductor
    turn = agent.create_turn(
        prompts=["fallback test"],
        run_ctx=_make_run_ctx(),
        message_history=[],
    )
    assert isinstance(turn, ACPTurn)
    assert turn._prompts == ["fallback test"]
    assert turn._hooks is None  # no hooks configured

    # Execute the turn with a real mock client
    client = MockACPClient(
        updates=[_text_update("fallback output"), TurnCompleteUpdate()],
        messages=[_text_update("fallback output")],
    )
    turn_with_client = _make_turn_with_client(
        client,
        prompts=["fallback test"],
    )

    events = [event async for event in turn_with_client.execute()]

    # Verify events are produced correctly
    delta_events = [e for e in events if isinstance(e, PartDeltaEvent)]
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]

    assert len(delta_events) >= 1
    assert len(complete_events) == 1
    assert complete_events[0].message.content == "fallback output"

    # Verify final_message
    assert turn_with_client._final_message is not None
    assert turn_with_client._final_message.role == "assistant"
    assert turn_with_client._final_message.content == "fallback output"
