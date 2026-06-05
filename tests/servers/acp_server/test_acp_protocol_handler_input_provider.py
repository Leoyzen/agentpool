"""Tests for ACPProtocolHandler input_provider propagation.

Verifies that elicitation and tool confirmations flow through the ACP
protocol instead of falling back to StdlibInputProvider when using the
SessionPool path.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import TextContentBlock
from agentpool.orchestrator.run import RunHandle
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
    session_pool.receive_request = AsyncMock()
    session_pool.event_bus = MagicMock()

    # Event consumer loop should exit immediately in tests
    async def _mock_queue_get():
        return None  # sentinel to stop consumer loop

    mock_queue = MagicMock()
    mock_queue.get = _mock_queue_get
    session_pool.event_bus.subscribe = AsyncMock(return_value=mock_queue)
    session_pool.event_bus.close_session = AsyncMock()
    session_pool.event_bus.unsubscribe = AsyncMock()

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
        client_capabilities=None,
    )


@pytest.fixture
def handler_with_elicitation(
    mock_pool: MagicMock,
    mock_event_converter: MagicMock,
    mock_client: MagicMock,
) -> ACPProtocolHandler:
    """Return an ACPProtocolHandler with elicitation capabilities."""
    from acp.schema.capabilities import ClientCapabilities, ElicitationCapabilities

    return ACPProtocolHandler(
        agent_pool=mock_pool,
        event_converter=mock_event_converter,
        client=mock_client,
        client_capabilities=ClientCapabilities(
            elicitation=ElicitationCapabilities(form=True, url=True)
        ),
    )


class TestHandlePromptInputProvider:
    """RED FLAG: input_provider must be passed to SessionPool.receive_request."""

    @pytest.mark.anyio
    async def test_handle_prompt_passes_acp_input_provider(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """When handle_prompt() is called, an ACPInputProvider is created
        and passed to SessionPool.receive_request() so elicitation goes
        through the ACP protocol."""
        prompt = [TextContentBlock(text="hello")]

        await handler.handle_prompt("sess-1", prompt)

        session_pool = mock_pool.session_pool
        assert session_pool.receive_request.called
        call_kwargs = session_pool.receive_request.call_args.kwargs
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
        call_kwargs = session_pool.receive_request.call_args.kwargs
        input_provider = call_kwargs["input_provider"]
        assert input_provider.session.requests is not None

    @pytest.mark.anyio
    async def test_handle_prompt_input_provider_has_capabilities(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """The ACPInputProvider must have client_capabilities so
        capability-gated elicitation paths work correctly.
        When no capabilities are passed, elicitation is not advertised."""
        prompt = [TextContentBlock(text="hello")]

        await handler.handle_prompt("sess-1", prompt)

        session_pool = mock_pool.session_pool
        call_kwargs = session_pool.receive_request.call_args.kwargs
        input_provider = call_kwargs["input_provider"]
        assert input_provider.session.client_capabilities is not None
        assert input_provider.session.client_capabilities.elicitation is None

    @pytest.mark.anyio
    async def test_handle_prompt_forwards_elicitation_capabilities(
        self,
        handler_with_elicitation: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """When the handler is created with elicitation capabilities,
        the ACPInputProvider must advertise them so elicitation/create
        is used instead of falling back to request_permission."""
        prompt = [TextContentBlock(text="hello")]

        await handler_with_elicitation.handle_prompt("sess-1", prompt)

        session_pool = mock_pool.session_pool
        call_kwargs = session_pool.receive_request.call_args.kwargs
        input_provider = call_kwargs["input_provider"]
        caps = input_provider.session.client_capabilities
        assert caps.elicitation is not None
        assert caps.elicitation.form is True
        assert caps.elicitation.url is True

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
        assert not mock_pool.session_pool.receive_request.called

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
        an empty ClientCapabilities instance with no elicitation support."""
        from acp.schema.capabilities import ClientCapabilities

        proxy = _ACPSessionProxy(requests=MagicMock())
        assert isinstance(proxy.client_capabilities, ClientCapabilities)
        assert proxy.client_capabilities.elicitation is None

    def test_proxy_accepts_custom_capabilities(self) -> None:
        """_ACPSessionProxy can be created with custom client capabilities."""
        from acp.schema.capabilities import ClientCapabilities

        caps = ClientCapabilities(fs=None, terminal=True)
        proxy = _ACPSessionProxy(requests=MagicMock(), client_capabilities=caps)
        assert proxy.client_capabilities is caps


class TestEventConsumerConverterFlag:
    """Tests that _event_consumer_loop passes client_supports_turn_complete to ACPEventConverter."""

    @pytest.mark.anyio
    async def test_event_consumer_passes_turn_complete_true(
        self,
        mock_pool: MagicMock,
        mock_event_converter: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """When client supports turn_complete, converter is created with flag=True."""
        from acp.schema.capabilities import ClientCapabilities
        from agentpool_server.acp_server.handler import ACPEventConverter

        handler = ACPProtocolHandler(
            agent_pool=mock_pool,
            event_converter=mock_event_converter,
            client=mock_client,
            client_capabilities=ClientCapabilities(turn_complete=True),
        )

        with patch.object(
            ACPEventConverter, "__init__", return_value=None
        ) as mock_init:
            await handler._event_consumer_loop("sess-1")

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs.get("client_supports_turn_complete") is True


class TestHandlePromptBlockingBehavior:
    """Tests for ACPProtocolHandler.handle_prompt() blocking on RunHandle.complete_event."""

    @pytest.mark.anyio
    async def test_legacy_client_blocks_until_run_completes(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """Legacy clients block until the run's complete_event is set."""
        event = asyncio.Event()
        run_handle = RunHandle(
            run_id="run-1",
            session_id="sess-1",
            agent_type="native",
            complete_event=event,
        )
        mock_pool.session_pool.receive_request = AsyncMock(return_value=run_handle)

        prompt = [TextContentBlock(text="hello")]
        task = asyncio.create_task(handler.handle_prompt("sess-1", prompt))

        # Yield so the task reaches the wait()
        await asyncio.sleep(0)
        assert not task.done(), "Should block until complete_event is set"

        event.set()
        result = await task
        assert result is not None
        assert result.stop_reason == "end_turn"

    @pytest.mark.anyio
    async def test_modern_client_returns_immediately(
        self,
        mock_pool: MagicMock,
        mock_event_converter: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """Modern clients with turn_complete=True return without waiting."""
        from acp.schema.capabilities import ClientCapabilities

        handler = ACPProtocolHandler(
            agent_pool=mock_pool,
            event_converter=mock_event_converter,
            client=mock_client,
            client_capabilities=ClientCapabilities(turn_complete=True),
        )

        event = asyncio.Event()
        run_handle = RunHandle(
            run_id="run-1",
            session_id="sess-1",
            agent_type="native",
            complete_event=event,
        )
        mock_pool.session_pool.receive_request = AsyncMock(return_value=run_handle)

        prompt = [TextContentBlock(text="hello")]
        with patch.object(event, "wait", new_callable=AsyncMock) as mock_wait:
            result = await handler.handle_prompt("sess-1", prompt)

        assert result is not None
        assert result.stop_reason == "end_turn"
        mock_wait.assert_not_awaited()

    @pytest.mark.anyio
    async def test_legacy_client_cancelled_during_wait(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """If the wait is cancelled, handler returns stop_reason='cancelled'."""
        event = asyncio.Event()
        run_handle = RunHandle(
            run_id="run-1",
            session_id="sess-1",
            agent_type="native",
            complete_event=event,
        )
        mock_pool.session_pool.receive_request = AsyncMock(return_value=run_handle)

        prompt = [TextContentBlock(text="hello")]
        with patch.object(event, "wait", side_effect=asyncio.CancelledError):
            result = await handler.handle_prompt("sess-1", prompt)

        assert result is not None
        assert result.stop_reason == "cancelled"

    @pytest.mark.anyio
    async def test_legacy_client_missing_capabilities_defaults_to_blocking(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """When client_capabilities is None, handler defaults to blocking."""
        event = asyncio.Event()
        run_handle = RunHandle(
            run_id="run-1",
            session_id="sess-1",
            agent_type="native",
            complete_event=event,
        )
        mock_pool.session_pool.receive_request = AsyncMock(return_value=run_handle)

        prompt = [TextContentBlock(text="hello")]
        task = asyncio.create_task(handler.handle_prompt("sess-1", prompt))

        await asyncio.sleep(0)
        assert not task.done(), "Should block when client_capabilities is None"

        event.set()
        result = await task
        assert result is not None
        assert result.stop_reason == "end_turn"

    @pytest.mark.anyio
    async def test_legacy_client_run_completes_quickly(
        self,
        handler: ACPProtocolHandler,
        mock_pool: MagicMock,
    ) -> None:
        """If the run is already complete, legacy client returns promptly."""
        event = asyncio.Event()
        event.set()
        run_handle = RunHandle(
            run_id="run-1",
            session_id="sess-1",
            agent_type="native",
            complete_event=event,
        )
        mock_pool.session_pool.receive_request = AsyncMock(return_value=run_handle)

        prompt = [TextContentBlock(text="hello")]
        result = await handler.handle_prompt("sess-1", prompt)
        assert result is not None
        assert result.stop_reason == "end_turn"

    @pytest.mark.anyio
    async def test_event_consumer_defaults_turn_complete_when_no_capabilities(
        self,
        mock_pool: MagicMock,
        mock_event_converter: MagicMock,
        mock_client: MagicMock,
    ) -> None:
        """When client_capabilities is None, converter defaults to flag=False."""
        from agentpool_server.acp_server.handler import ACPEventConverter

        handler = ACPProtocolHandler(
            agent_pool=mock_pool,
            event_converter=mock_event_converter,
            client=mock_client,
            client_capabilities=None,
        )

        with patch.object(
            ACPEventConverter, "__init__", return_value=None
        ) as mock_init:
            await handler._event_consumer_loop("sess-1")

        mock_init.assert_called_once()
        call_kwargs = mock_init.call_args.kwargs
        assert call_kwargs.get("client_supports_turn_complete") is False
