"""Tests for DispatchAgent version routing and delegation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.exceptions import RequestError
from agentpool_server.acp_server.shared.dispatch_agent import DispatchAgent


@dataclass
class MockBaseAgent:
    """Minimal BaseAgent mock for DispatchAgent construction."""
    name: str = "test-agent"
    agent_pool: Any = None


@dataclass
class MockPool:
    """Minimal pool mock."""
    manifest: Any = None


@dataclass
class MockManifest:
    """Minimal manifest mock."""
    acp: Any = None


@dataclass
class MockACPConfig:
    """Minimal ACP config mock."""
    use_session_pool: bool = False


def _make_dispatch_agent(use_session_pool: bool = False) -> DispatchAgent:
    """Create a DispatchAgent with minimal mocks."""
    acp_config = MockACPConfig(use_session_pool=use_session_pool)
    manifest = MockManifest(acp=acp_config)
    pool = MockPool(manifest=manifest)
    agent = MockBaseAgent(name="test", agent_pool=pool)
    client = MagicMock()
    return DispatchAgent(client=client, default_agent=agent)


class TestDispatchAgentVersionRouting:
    """Verify DispatchAgent routes to v1 or v2 based on protocolVersion."""

    @pytest.mark.unit
    async def test_v1_initialize_creates_v1_delegate(self) -> None:
        from unittest.mock import AsyncMock, patch

        from acp.schema import InitializeRequest

        dispatch = _make_dispatch_agent(use_session_pool=False)
        mock_response = MagicMock()
        mock_response.protocol_version = 1
        mock_response.field_meta = None

        with patch.object(dispatch, "_create_v1_delegate") as mock_create:
            mock_v1 = MagicMock()
            mock_v1.initialize = AsyncMock(return_value=mock_response)
            mock_create.return_value = mock_v1
            req = InitializeRequest(protocol_version=1)
            result = await dispatch.initialize(req)
            mock_create.assert_called_once()
            assert result is mock_response

    @pytest.mark.unit
    async def test_v2_initialize_creates_v2_delegate_when_session_pool_enabled(self) -> None:
        from unittest.mock import AsyncMock, patch

        from acp.schema import InitializeRequest

        dispatch = _make_dispatch_agent(use_session_pool=True)
        mock_response = MagicMock()
        mock_response.protocol_version = 2
        mock_response.field_meta = None

        with patch.object(dispatch, "_create_v2_delegate") as mock_create:
            mock_v2 = MagicMock()
            mock_v2.initialize = AsyncMock(return_value=mock_response)
            mock_create.return_value = mock_v2
            req = InitializeRequest(protocol_version=2)
            result = await dispatch.initialize(req)
            mock_create.assert_called_once()
            assert result is mock_response

    @pytest.mark.unit
    async def test_v2_initialize_degrades_to_v1_when_session_pool_disabled(self) -> None:
        from unittest.mock import AsyncMock, patch

        from acp.schema import InitializeRequest

        dispatch = _make_dispatch_agent(use_session_pool=False)
        mock_response = MagicMock()
        mock_response.protocol_version = 1
        mock_response.field_meta = None

        with patch.object(dispatch, "_create_v1_delegate") as mock_create:
            mock_v1 = MagicMock()
            mock_v1.initialize = AsyncMock(return_value=mock_response)
            mock_create.return_value = mock_v1
            req = InitializeRequest(protocol_version=2)
            result = await dispatch.initialize(req)
            mock_create.assert_called_once()
            assert result.protocol_version == 2
            assert result.field_meta is not None
            assert result.field_meta.get("fallback") is True

    @pytest.mark.unit
    async def test_v0_initialize_raises_request_error(self) -> None:
        from acp.schema import InitializeRequest

        dispatch = _make_dispatch_agent(use_session_pool=False)
        req = InitializeRequest(protocol_version=0)
        with pytest.raises(RequestError):
            await dispatch.initialize(req)


class TestDispatchAgentDelegation:
    """Verify DispatchAgent delegates methods after initialize."""

    @pytest.mark.unit
    async def test_prompt_delegated(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.prompt = AsyncMock(return_value="prompt_result")
        result = await dispatch.prompt(MagicMock())
        dispatch._delegate.prompt.assert_called_once()
        assert result == "prompt_result"

    @pytest.mark.unit
    async def test_cancel_delegated(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.cancel = AsyncMock()
        await dispatch.cancel(MagicMock())
        dispatch._delegate.cancel.assert_called_once()

    @pytest.mark.unit
    async def test_new_session_delegated(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.new_session = AsyncMock(return_value="session_result")
        result = await dispatch.new_session(MagicMock())
        assert result == "session_result"

    @pytest.mark.unit
    async def test_close_session_delegated(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.close_session = AsyncMock(return_value="close_result")
        result = await dispatch.close_session(MagicMock())
        assert result == "close_result"

    @pytest.mark.unit
    async def test_close_delegated(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.close = AsyncMock()
        await dispatch.close()
        dispatch._delegate.close.assert_called_once()


class TestDispatchAgentMethodNames:
    """Verify DispatchAgent responds to both v1 and v2 method names."""

    @pytest.mark.unit
    async def test_authenticate_on_v1_delegate(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.authenticate = AsyncMock(return_value="auth_result")
        result = await dispatch.authenticate(MagicMock())
        assert result == "auth_result"

    @pytest.mark.unit
    async def test_auth_login_on_v2_delegate(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.auth_login = AsyncMock(return_value="login_result")
        result = await dispatch.auth_login(MagicMock())
        assert result == "login_result"

    @pytest.mark.unit
    async def test_auth_login_returns_none_on_v1_delegate(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock(spec=[])  # No auth_login attribute
        result = await dispatch.auth_login(MagicMock())
        assert result is None

    @pytest.mark.unit
    async def test_set_session_mode_on_v1_delegate(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.set_session_mode = AsyncMock(return_value="mode_result")
        result = await dispatch.set_session_mode(MagicMock())
        assert result == "mode_result"


class TestDispatchAgentGetattrFallback:
    """Verify __getattr__ forwards unknown attributes to delegate."""

    @pytest.mark.unit
    def test_getattr_forwards_to_delegate(self) -> None:
        dispatch = _make_dispatch_agent()
        dispatch._delegate = MagicMock()
        dispatch._delegate.some_custom_method = "custom"
        assert dispatch.some_custom_method == "custom"

    @pytest.mark.unit
    def test_getattr_raises_before_initialize(self) -> None:
        dispatch = _make_dispatch_agent()
        with pytest.raises(AttributeError):
            _ = dispatch.some_method  # noqa: F841

    @pytest.mark.unit
    def test_getattr_raises_for_underscore_names(self) -> None:
        dispatch = _make_dispatch_agent()
        with pytest.raises(AttributeError):
            _ = dispatch._private_attr  # noqa: F841
