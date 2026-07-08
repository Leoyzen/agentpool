"""Integration tests for AgentPoolACPAgent as a terminal agent in proxy chains.

Tests verify that AgentPoolACPAgent:
- Responds to the standard ``initialize`` method (terminal agent behavior)
- Does NOT handle ``proxy/initialize`` (that's a proxy-only method)
- Routes ``prompt()`` through ``ACPProtocolHandler.handle_prompt()``
- Works as the terminal agent in a Conductor chain (mocked subprocess)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.proxy.constants import PROXY_INITIALIZE
from acp.schema import (
    ClientCapabilities,
    Implementation,
    InitializeRequest,
    InitializeResponse,
    PromptResponse,
    TextContentBlock,
)


if TYPE_CHECKING:
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_init_request() -> InitializeRequest:
    """Create a minimal InitializeRequest for testing."""
    return InitializeRequest(
        protocol_version=1,
        client_capabilities=ClientCapabilities(),
        client_info=Implementation(name="test-client", version="0.1.0"),
    )


def _make_prompt_request(session_id: str, text: str) -> Any:
    """Create a minimal PromptRequest with a text content block."""
    from acp.schema import PromptRequest

    return PromptRequest(
        session_id=session_id,
        prompt=[TextContentBlock(type="text", text=text)],
    )


# ---------------------------------------------------------------------------
# Test 1: AgentPoolACPAgent handles ``initialize`` method
# ---------------------------------------------------------------------------


async def test_terminal_agent_responds_to_initialize(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """AgentPoolACPAgent handles the standard ``initialize`` method.

    The terminal agent must respond to ``initialize`` with an
    ``InitializeResponse`` containing the negotiated protocol version
    and agent capabilities. This is the standard ACP handshake that
    every terminal agent must support.
    """
    request = _make_init_request()

    response = await mock_acp_agent.initialize(request)

    assert isinstance(response, InitializeResponse)
    assert response.protocol_version == 1
    # AgentPoolACPAgent identifies itself as "agentpool" via agent_info
    assert response.agent_info is not None
    assert response.agent_info.name == "agentpool"
    assert response.agent_info.title == "AgentPool"
    # Capabilities should be advertised
    assert response.agent_capabilities is not None
    assert response.agent_capabilities.load_session is True
    # Session capabilities (list, resume, close, fork) are nested
    assert response.agent_capabilities.session_capabilities is not None
    assert response.agent_capabilities.session_capabilities.list is not None
    assert response.agent_capabilities.session_capabilities.resume is not None
    # After initialize, the agent should be marked as initialized
    assert mock_acp_agent._initialized is True


# ---------------------------------------------------------------------------
# Test 2: AgentPoolACPAgent does NOT handle ``proxy/initialize``
# ---------------------------------------------------------------------------


async def test_terminal_agent_does_not_handle_proxy_initialize(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """AgentPoolACPAgent does NOT handle ``proxy/initialize``.

    The ``proxy/initialize`` method (PROXY_INITIALIZE) is a proxy-chain
    extension method. Terminal agents are standard ACP agents and should
    not respond to it. The ``ext_method`` handler should return an empty
    dict for unknown extension methods, not a proxy initialization result.
    """
    # ext_method is the handler for extension methods like proxy/initialize
    result = await mock_acp_agent.ext_method(
        PROXY_INITIALIZE,
        {"interceptedMethods": ["session/prompt"]},
    )

    # Terminal agent's ext_method returns {} for unknown methods
    # (proxy/initialize is NOT a recognized extension method for terminal agents)
    assert result == {}


# ---------------------------------------------------------------------------
# Test 3: prompt() delegates to ACPProtocolHandler.handle_prompt()
# ---------------------------------------------------------------------------


async def test_terminal_agent_prompt_routes_through_handler(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """prompt() delegates to ACPProtocolHandler.handle_prompt().

    When ``_protocol_handler`` is set (SessionPool mode), ``prompt()``
    must delegate to ``handle_prompt()`` and return its result. This
    ensures the consolidated prompt handling path is used.
    """
    # Create a mock protocol handler
    expected_response = PromptResponse(stop_reason="end_turn")
    mock_handler = MagicMock()
    mock_handler.handle_prompt = AsyncMock(return_value=expected_response)

    # Inject the mock handler
    mock_acp_agent._protocol_handler = mock_handler
    mock_acp_agent._initialized = True

    prompt_request = _make_prompt_request("test-session-123", "Hello, agent!")

    response = await mock_acp_agent.prompt(prompt_request)

    # Verify handle_prompt was called with the session_id and prompt
    mock_handler.handle_prompt.assert_called_once_with(
        "test-session-123",
        prompt_request.prompt,
    )
    assert response is expected_response
    assert response.stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Test 3b: prompt() raises when no protocol handler is configured
# ---------------------------------------------------------------------------


async def test_terminal_agent_prompt_raises_without_handler(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """prompt() raises RuntimeError when no protocol handler is configured.

    After T22, the legacy ``process_prompt()`` path was removed. If
    ``_protocol_handler`` is None, ``prompt()`` must raise rather than
    silently fail.
    """
    # Ensure no protocol handler is set
    mock_acp_agent._protocol_handler = None
    mock_acp_agent._initialized = True

    prompt_request = _make_prompt_request("test-session-456", "Hello!")

    with pytest.raises(RuntimeError, match="No protocol handler configured"):
        await mock_acp_agent.prompt(prompt_request)


# ---------------------------------------------------------------------------
# Test 4: AgentPoolACPAgent works as terminal agent in a Conductor chain
# ---------------------------------------------------------------------------


async def test_terminal_agent_in_conductor_chain(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """AgentPoolACPAgent works as terminal agent in a Conductor chain.

    Simulates a Conductor with a single proxy that intercepts
    ``session/prompt``. The terminal agent (AgentPoolACPAgent) receives
    the forwarded prompt through its ``prompt()`` method, which delegates
    to the protocol handler.

    The subprocess is mocked — no real process is spawned.
    """
    from acp.conductor import Conductor

    # --- Set up the terminal agent (AgentPoolACPAgent) ---
    expected_response = PromptResponse(stop_reason="end_turn")
    mock_handler = MagicMock()
    mock_handler.handle_prompt = AsyncMock(return_value=expected_response)
    mock_acp_agent._protocol_handler = mock_handler
    mock_acp_agent._initialized = True

    # --- Set up a fake proxy that intercepts session/prompt ---
    class _FakeProxy:
        """Fake proxy that intercepts session/prompt and forwards params."""

        def __init__(self) -> None:
            self.successor_calls: list[
                tuple[str, dict[str, Any], dict[str, Any]]
            ] = []

        def proxy_initialize(self) -> list[str]:
            return ["session/prompt"]

        async def proxy_successor(
            self,
            method: str,
            params: dict[str, Any],
            meta: dict[str, Any],
        ) -> dict[str, Any]:
            self.successor_calls.append((method, params, meta))
            # Modify the prompt text to prove the proxy ran
            params["prompt"] = [{"type": "text", "text": "proxied: hello"}]
            return params

    fake_proxy = _FakeProxy()

    # --- Create a Conductor with the fake proxy ---
    conductor = Conductor(
        name="test_terminal",
        command="echo",
        args=["dummy"],
        proxy_chain=[fake_proxy],
    )

    # --- Set up Conductor internal state without spawning a subprocess ---
    # Mock the connection so send_request routes to our terminal agent
    async def _fake_send_request(method: str, params: dict[str, Any]) -> Any:
        """Simulate the terminal agent receiving a JSON-RPC request."""
        if method == "initialize":
            return {"result": {"protocolVersion": 1, "name": "agentpool"}}
        if method == "session/prompt":
            # Build a PromptRequest from the JSON-RPC params and call the agent
            session_id = params.get("sessionId", "conductor-session")
            prompt_blocks = [
                TextContentBlock.model_validate(b)
                for b in params.get("prompt", [])
            ]
            from acp.schema import PromptRequest

            request = PromptRequest(session_id=session_id, prompt=prompt_blocks)
            response = await mock_acp_agent.prompt(request)
            return {"result": {"stopReason": response.stop_reason}}
        return {"result": {}}

    mock_connection = MagicMock()
    mock_connection.send_request = _fake_send_request
    conductor._connection = mock_connection
    conductor._conductor_initialized = True

    # Populate intercepted_methods as _initialize_chain would
    conductor._intercepted_methods = [fake_proxy.proxy_initialize()]
    conductor._chain_initialized = True

    # --- Route a session/prompt through the Conductor ---
    route_params: dict[str, Any] = {
        "sessionId": "conductor-session",
        "prompt": [{"type": "text", "text": "hello"}],
    }
    route_meta: dict[str, Any] = {"direction": "forward"}

    result = await conductor._route_message("session/prompt", route_params, route_meta)

    # --- Assertions ---
    # 1. The proxy should have intercepted and modified the prompt
    assert len(fake_proxy.successor_calls) == 1
    intercepted_method, intercepted_params, _ = fake_proxy.successor_calls[0]
    assert intercepted_method == "session/prompt"
    assert intercepted_params["prompt"][0]["text"] == "proxied: hello"

    # 2. The terminal agent's handler should have been called with the proxied prompt
    mock_handler.handle_prompt.assert_called_once()
    call_args = mock_handler.handle_prompt.call_args
    forwarded_prompt = call_args[0][1]  # Second positional arg = prompt blocks
    assert len(forwarded_prompt) == 1
    assert forwarded_prompt[0].text == "proxied: hello"

    # 3. The Conductor should return the terminal agent's response
    assert "result" in result
    assert result["result"]["stopReason"] == "end_turn"
