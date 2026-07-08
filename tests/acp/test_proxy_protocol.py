"""Tests for the ACP proxy chain protocol package.

Covers: Proxy protocol (runtime_checkable, isinstance), constants,
ProxySideConnection dispatch and forwarding.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.proxy import PROXY_INITIALIZE, PROXY_SUCCESSOR, Proxy, ProxySideConnection
from acp.proxy.constants import PROXY_INITIALIZE as CONST_INIT, PROXY_SUCCESSOR as CONST_SUCC


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_proxy_initialize_constant() -> None:
    """PROXY_INITIALIZE constant matches expected wire method name."""
    assert PROXY_INITIALIZE == "proxy/initialize"
    assert CONST_INIT == "proxy/initialize"


def test_proxy_successor_constant() -> None:
    """PROXY_SUCCESSOR constant matches expected wire method name."""
    assert PROXY_SUCCESSOR == "proxy/successor"
    assert CONST_SUCC == "proxy/successor"


# ---------------------------------------------------------------------------
# Fake Proxy implementations for testing
# ---------------------------------------------------------------------------


class FakeProxy:
    """Fake proxy implementing the Proxy protocol."""

    def __init__(
        self,
        intercepted_methods: list[str] | None = None,
        successor_response: dict[str, Any] | None = None,
    ) -> None:
        self._intercepted = intercepted_methods or []
        self._successor_response = successor_response or {"result": "ok"}
        self.init_called = False
        self.successor_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def proxy_initialize(self) -> list[str]:
        self.init_called = True
        return self._intercepted

    def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        self.successor_calls.append((method, params, meta))
        return self._successor_response


class FailingProxy:
    """Proxy that raises during proxy_initialize."""

    def proxy_initialize(self) -> list[str]:
        msg = "init failed"
        raise RuntimeError(msg)

    def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        msg = "should not reach"
        raise RuntimeError(msg)


class SuccessorFailingProxy:
    """Proxy that raises during proxy_successor."""

    def __init__(self, intercepted_methods: list[str] | None = None) -> None:
        self._intercepted = intercepted_methods or ["session/prompt"]

    def proxy_initialize(self) -> list[str]:
        return self._intercepted

    def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        msg = "successor failed"
        raise RuntimeError(msg)


# ---------------------------------------------------------------------------
# Proxy protocol tests
# ---------------------------------------------------------------------------


def test_proxy_protocol_is_runtime_checkable() -> None:
    """Proxy protocol supports isinstance checks (runtime_checkable)."""
    proxy = FakeProxy()
    assert isinstance(proxy, Proxy)


def test_proxy_protocol_rejects_non_implementing_class() -> None:
    """Objects not implementing Proxy methods fail isinstance."""
    assert not isinstance(42, Proxy)
    assert not isinstance("hello", Proxy)
    assert not isinstance(object(), Proxy)


def test_proxy_protocol_rejects_partial_implementation() -> None:
    """Class with only proxy_initialize (missing proxy_successor) is not a Proxy."""

    class PartialProxy:
        def proxy_initialize(self) -> list[str]:
            return []

    assert not isinstance(PartialProxy(), Proxy)


def test_fake_proxy_proxy_initialize_returns_list() -> None:
    """proxy_initialize returns list[str] of intercepted methods."""
    proxy = FakeProxy(intercepted_methods=["session/prompt", "session/update"])
    result = proxy.proxy_initialize()
    assert isinstance(result, list)
    assert all(isinstance(m, str) for m in result)
    assert result == ["session/prompt", "session/update"]


def test_fake_proxy_proxy_initialize_empty_list() -> None:
    """proxy_initialize can return empty list (no interception)."""
    proxy = FakeProxy(intercepted_methods=[])
    result = proxy.proxy_initialize()
    assert result == []


def test_fake_proxy_proxy_successor_returns_dict() -> None:
    """proxy_successor returns dict[str, Any] response."""
    proxy = FakeProxy(successor_response={"result": {"text": "hello"}})
    result = proxy.proxy_successor(
        "session/prompt",
        {"prompt": []},
        {"direction": "forward"},
    )
    assert isinstance(result, dict)
    assert result == {"result": {"text": "hello"}}


def test_fake_proxy_proxy_successor_records_calls() -> None:
    """proxy_successor records all calls for inspection."""
    proxy = FakeProxy()
    proxy.proxy_successor("session/prompt", {"key": "val"}, {"meta": "data"})
    assert len(proxy.successor_calls) == 1
    method, params, meta = proxy.successor_calls[0]
    assert method == "session/prompt"
    assert params == {"key": "val"}
    assert meta == {"meta": "data"}


# ---------------------------------------------------------------------------
# ProxySideConnection tests
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_connection() -> MagicMock:
    """Create a mock Connection for ProxySideConnection."""
    conn = MagicMock()
    conn.send_request = AsyncMock(return_value={"result": "forwarded"})
    conn.send_notification = AsyncMock()
    conn.close = AsyncMock()
    return conn


@pytest.fixture
def fake_proxy() -> FakeProxy:
    """Create a FakeProxy for testing."""
    return FakeProxy(
        intercepted_methods=["session/prompt"],
        successor_response={"result": "proxied"},
    )


async def test_proxy_side_connection_handle_initialize(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """handle_proxy_method dispatches proxy/initialize correctly."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    result = await psc.handle_proxy_method(PROXY_INITIALIZE, {})
    assert fake_proxy.init_called
    assert result == {"intercepted_methods": ["session/prompt"]}


async def test_proxy_side_connection_handle_successor(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """handle_proxy_method dispatches proxy/successor correctly."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    params: dict[str, Any] = {
        "method": "session/prompt",
        "prompt": [],
        "_meta": {"direction": "forward"},
    }
    result = await psc.handle_proxy_method(PROXY_SUCCESSOR, params)
    assert result == {"result": "proxied"}
    assert len(fake_proxy.successor_calls) == 1
    method, _p, meta = fake_proxy.successor_calls[0]
    assert method == "session/prompt"
    assert meta == {"direction": "forward"}


async def test_proxy_side_connection_handle_unknown_method(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """handle_proxy_method raises ValueError for unknown methods."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    with pytest.raises(ValueError, match="Unknown proxy method"):
        await psc.handle_proxy_method("unknown/method", {})


async def test_proxy_side_connection_send_request_forwards(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """send_request forwards to wrapped connection."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    result = await psc.send_request("session/prompt", {"key": "val"})
    mock_connection.send_request.assert_called_once_with("session/prompt", {"key": "val"})
    assert result == {"result": "forwarded"}


async def test_proxy_side_connection_send_request_default_params(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """send_request uses empty dict when params is None."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    await psc.send_request("initialize")
    mock_connection.send_request.assert_called_once_with("initialize", {})


async def test_proxy_side_connection_send_notification_forwards(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """send_notification forwards to wrapped connection."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    await psc.send_notification("session/update", {"key": "val"})
    mock_connection.send_notification.assert_called_once_with(
        "session/update", {"key": "val"}
    )


async def test_proxy_side_connection_send_notification_default_params(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """send_notification uses empty dict when params is None."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    await psc.send_notification("session/update")
    mock_connection.send_notification.assert_called_once_with("session/update", {})


async def test_proxy_side_connection_close(
    mock_connection: MagicMock,
    fake_proxy: FakeProxy,
) -> None:
    """Close delegates to wrapped connection."""
    psc = ProxySideConnection(mock_connection, fake_proxy)
    await psc.close()
    mock_connection.close.assert_called_once()


async def test_proxy_side_connection_successor_without_meta(
    mock_connection: MagicMock,
) -> None:
    """handle_proxy_method for successor handles missing _meta gracefully."""
    proxy = FakeProxy(successor_response={"result": "ok"})
    psc = ProxySideConnection(mock_connection, proxy)
    params: dict[str, Any] = {"method": "session/prompt", "prompt": []}
    result = await psc.handle_proxy_method(PROXY_SUCCESSOR, params)
    assert result == {"result": "ok"}
    assert len(proxy.successor_calls) == 1
    _, _, meta = proxy.successor_calls[0]
    assert meta == {}
