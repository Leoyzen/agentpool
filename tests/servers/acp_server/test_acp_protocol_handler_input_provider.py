"""Tests for ACPProtocolHandler input_provider propagation.

Verifies that elicitation and tool confirmations flow through the ACP
protocol instead of falling back to StdlibInputProvider when using the
SessionPool path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import TextContentBlock
from agentpool_server.acp_server.handler import ACPProtocolHandler, _ACPSessionProxy
from agentpool_server.acp_server.input_provider import ACPInputProvider


pytestmark = pytest.mark.unit


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with SessionPool enabled."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.metadata = {"use_session_pool": True}

    session_pool = MagicMock()
    session_pool.create_session = AsyncMock()
    session_pool.process_prompt = AsyncMock()
    session_pool.event_bus = MagicMock()

    # Event consumer loop should exit immediately in tests
    async def _mock_queue_get():
        return None  # sentinel to stop consumer loop

    mock_queue = MagicMock()
    mock_queue.get = _mock_queue_get
    session_pool.event_bus.subscribe = AsyncMock(return_value=mock_queue)
    session_pool.event_bus.close_session = AsyncMock()

    pool.session_pool = session_pool
    return pool


@pytest.fixture
def mock_event_converter() -> MagicMock:
    """Return a mocked ACPEventConverter."""
    converter = MagicMock()
    converter.subagent_display_mode = "tool_box"
    return converter


@pytest.fixture
def mock_client() -> MagicMock:
    """Return a mocked ACP Client."""
    return MagicMock()


@pytest.fixture
def handler(
    mock_pool: MagicMock,
    mock_event_converter: MagicMock,
    mock_client: MagicMock,
) -> ACPProtocolHandler:
    """Return an ACPProtocolHandler backed by mocked dependencies."""
    return ACPProtocolHandler(
        agent_pool=mock_pool,
        event_converter=mock_event_converter,
        client=mock_client,
    )


class TestHandlePromptInputProvider:
    """RED FLAG: input_provider must be passed to SessionPool.process_prompt."""

    @pytest.mark.anyio
    async def test_handle_prompt_passes_acp_input_provider(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """When handle_prompt() is called, an ACPInputProvider is created
        and passed to SessionPool.process_prompt() so elicitation goes
        through the ACP protocol."""
        prompt = [TextContentBlock(text="hello")]

        await handler.handle_prompt("sess-1", prompt)

        session_pool = mock_pool.session_pool
        assert session_pool.process_prompt.called
        call_kwargs = session_pool.process_prompt.call_args.kwargs
        assert "input_provider" in call_kwargs
        assert isinstance(call_kwargs["input_provider"], ACPInputProvider)

    @pytest.mark.anyio
    async def test_handle_prompt_input_provider_has_requests(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """The ACPInputProvider must have a requests object wired to
        the ACP client so request_permission / elicitation_create work."""
        prompt = [TextContentBlock(text="hello")]

        await handler.handle_prompt("sess-1", prompt)

        session_pool = mock_pool.session_pool
        call_kwargs = session_pool.process_prompt.call_args.kwargs
        input_provider = call_kwargs["input_provider"]
        assert input_provider.session.requests is not None

    @pytest.mark.anyio
    async def test_handle_prompt_input_provider_has_capabilities(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """The ACPInputProvider must have client_capabilities so
        capability-gated elicitation paths work correctly."""
        prompt = [TextContentBlock(text="hello")]

        await handler.handle_prompt("sess-1", prompt)

        session_pool = mock_pool.session_pool
        call_kwargs = session_pool.process_prompt.call_args.kwargs
        input_provider = call_kwargs["input_provider"]
        assert input_provider.session.client_capabilities is not None

    @pytest.mark.anyio
    async def test_handle_prompt_skips_when_canary_disabled(
        self,
        mock_pool: MagicMock,
        mock_event_converter: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """When the canary flag is off, handle_prompt returns None and
        does not create an input_provider."""
        mock_pool.main_agent.metadata = {"use_session_pool": False}
        handler = ACPProtocolHandler(
            agent_pool=mock_pool,
            event_converter=mock_event_converter,
            client=mock_client,
        )

        prompt = [TextContentBlock(text="hello")]
        result = await handler.handle_prompt("sess-1", prompt)

        assert result is None
        assert not mock_pool.session_pool.process_prompt.called

    @pytest.mark.anyio
    async def test_handle_prompt_skips_when_session_pool_missing(
        self,
        mock_pool: MagicMock,
        mock_event_converter: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """When SessionPool is not available, handle_prompt returns early."""
        mock_pool.session_pool = None
        handler = ACPProtocolHandler(
            agent_pool=mock_pool,
            event_converter=mock_event_converter,
            client=mock_client,
        )

        prompt = [TextContentBlock(text="hello")]
        result = await handler.handle_prompt("sess-1", prompt)

        assert result is not None
        assert result.stop_reason == "end_turn"


class TestACPSessionProxy:
    """Tests for the lightweight _ACPSessionProxy."""

    def test_proxy_exposes_requests(self) -> None:
        """_ACPSessionProxy.requests returns the injected requests object."""
        requests = MagicMock()
        proxy = _ACPSessionProxy(requests=requests)
        assert proxy.requests is requests

    def test_proxy_defaults_capabilities(self) -> None:
        """When no capabilities are given, _ACPSessionProxy defaults to
        an empty ClientCapabilities instance."""
        from acp.schema.capabilities import ClientCapabilities

        proxy = _ACPSessionProxy(requests=MagicMock())
        assert isinstance(proxy.client_capabilities, ClientCapabilities)

    def test_proxy_accepts_custom_capabilities(self) -> None:
        """_ACPSessionProxy can be created with custom client capabilities."""
        from acp.schema.capabilities import ClientCapabilities

        caps = ClientCapabilities(fs=None, terminal=True)
        proxy = _ACPSessionProxy(requests=MagicMock(), client_capabilities=caps)
        assert proxy.client_capabilities is caps
