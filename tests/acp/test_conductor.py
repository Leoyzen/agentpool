"""Tests for the ACP Conductor — chain init, routing, passthrough, errors.

Tests cover T9 (chain initialization), T10 (message routing), T11 (_step),
and context manager lifecycle. All tests use fake/mock proxies and connections
— NO real subprocess is spawned.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.conductor import Conductor, ConductorConfig
from acp.exceptions import RequestError
from acp.proxy.protocol import Proxy


# ---------------------------------------------------------------------------
# Fake Proxy for testing
# ---------------------------------------------------------------------------


class FakeProxy:
    """Fake proxy implementing the Proxy protocol."""

    def __init__(
        self,
        intercepted_methods: list[str] | None = None,
        successor_response: dict[str, Any] | None = None,
        init_error: Exception | None = None,
        successor_error: Exception | None = None,
    ) -> None:
        self._intercepted = intercepted_methods or []
        self._successor_response = successor_response or {"result": "ok"}
        self._init_error = init_error
        self._successor_error = successor_error
        self.init_called = False
        self.init_call_count = 0
        self.successor_calls: list[tuple[str, dict[str, Any], dict[str, Any]]] = []

    def proxy_initialize(self) -> list[str]:
        self.init_called = True
        self.init_call_count += 1
        if self._init_error is not None:
            raise self._init_error
        return self._intercepted

    async def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        self.successor_calls.append((method, params, meta))
        if self._successor_error is not None:
            raise self._successor_error
        return self._successor_response


class SuccessorFailingProxy:
    """Proxy that raises during proxy_successor."""

    def __init__(self, intercepted_methods: list[str] | None = None) -> None:
        self._intercepted = intercepted_methods or ["session/prompt"]

    def proxy_initialize(self) -> list[str]:
        return self._intercepted

    async def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        msg = "successor failed"
        raise RuntimeError(msg)


class RequestErrorProxy:
    """Proxy that raises RequestError during proxy_successor."""

    def __init__(self, intercepted_methods: list[str] | None = None) -> None:
        self._intercepted = intercepted_methods or ["session/prompt"]

    def proxy_initialize(self) -> list[str]:
        return self._intercepted

    async def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        raise RequestError(-32001, "Custom proxy error", {"detail": "blocked"})


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_conductor(
    proxy_chain: list[Any] | None = None,
    client_handler: Any | None = None,
) -> Conductor:
    """Create a Conductor without entering the context manager.

    Bypasses __aenter__ so no subprocess is spawned.
    """
    return Conductor(
        name="test_conductor",
        command="echo",
        args=["hello"],
        proxy_chain=proxy_chain,
        client_handler=client_handler,
    )


def _setup_initialized_conductor(
    proxy_chain: list[Any] | None = None,
    connection: Any | None = None,
) -> Conductor:
    """Create a Conductor with internal state set up for method testing.

    Sets _connection, _intercepted_methods, _chain_initialized, and
    _conductor_initialized directly — bypassing __aenter__.
    """
    conductor = _make_conductor(proxy_chain=proxy_chain)
    conductor._connection = connection or MagicMock()
    conductor._conductor_initialized = True

    # Populate intercepted_methods from proxies
    for proxy in (proxy_chain or []):
        intercepted = proxy.proxy_initialize()
        conductor._intercepted_methods.append(intercepted)
    conductor._chain_initialized = True

    return conductor


# ---------------------------------------------------------------------------
# ConductorConfig tests
# ---------------------------------------------------------------------------


def test_conductor_config_defaults() -> None:
    """ConductorConfig has sensible defaults for optional fields."""
    config = ConductorConfig(command="goose")
    assert config.command == "goose"
    assert config.args == []
    assert config.env is None
    assert config.cwd is None


def test_conductor_config_full() -> None:
    """ConductorConfig accepts all fields."""
    config = ConductorConfig(
        command="goose",
        args=["acp"],
        env={"FOO": "bar"},
        cwd="/tmp",
    )
    assert config.command == "goose"
    assert config.args == ["acp"]
    assert config.env == {"FOO": "bar"}
    assert config.cwd == "/tmp"


# ---------------------------------------------------------------------------
# Conductor __init__ tests
# ---------------------------------------------------------------------------


def test_conductor_init_defaults() -> None:
    """Conductor initializes with correct defaults."""
    conductor = Conductor(name="test", command="echo")
    assert conductor.name == "test"
    assert conductor.config.command == "echo"
    assert conductor.config.args == []
    assert conductor.proxy_chain == []
    assert conductor.client_handler is None
    assert conductor.connection is None
    assert conductor.process is None
    assert not conductor.is_initialized
    assert conductor._intercepted_methods == []
    assert not conductor._chain_initialized
    assert conductor.agent_type == "acp"


def test_conductor_init_with_proxy_chain() -> None:
    """Conductor stores proxy chain from constructor."""
    proxy = FakeProxy(intercepted_methods=["session/prompt"])
    conductor = Conductor(name="test", command="echo", proxy_chain=[proxy])
    assert len(conductor.proxy_chain) == 1
    assert conductor.proxy_chain[0] is proxy


def test_conductor_init_with_args_and_env() -> None:
    """Conductor passes args, env, cwd to ConductorConfig."""
    conductor = Conductor(
        name="test",
        command="goose",
        args=["acp", "--debug"],
        env={"PATH": "/usr/bin"},
        cwd="/home/user",
    )
    assert conductor.config.args == ["acp", "--debug"]
    assert conductor.config.env == {"PATH": "/usr/bin"}
    assert conductor.config.cwd == "/home/user"


def test_conductor_init_owns_handler_when_none() -> None:
    """Conductor owns handler lifecycle when client_handler is None."""
    conductor = Conductor(name="test", command="echo")
    assert conductor._owns_handler is True


def test_conductor_init_does_not_own_handler_when_provided() -> None:
    """Conductor does not own handler when externally provided."""
    handler = MagicMock()
    conductor = Conductor(name="test", command="echo", client_handler=handler)
    assert conductor._owns_handler is False
    assert conductor.client_handler is handler


def test_conductor_repr() -> None:
    """Conductor repr includes name, command, and status."""
    conductor = Conductor(name="my_agent", command="goose")
    repr_str = repr(conductor)
    assert "my_agent" in repr_str
    assert "goose" in repr_str
    assert "not initialized" in repr_str


# ---------------------------------------------------------------------------
# _is_terminal tests (T9)
# ---------------------------------------------------------------------------


def test_is_terminal_true_when_no_proxies() -> None:
    """_is_terminal returns True at index 0 when proxy chain is empty."""
    conductor = _make_conductor()
    assert conductor._is_terminal(0) is True


def test_is_terminal_true_at_chain_end() -> None:
    """_is_terminal returns True when index >= len(proxy_chain)."""
    proxy = FakeProxy()
    conductor = _make_conductor(proxy_chain=[proxy])
    assert conductor._is_terminal(1) is True
    assert conductor._is_terminal(2) is True


def test_is_terminal_false_for_proxy_positions() -> None:
    """_is_terminal returns False for proxy positions in the chain."""
    proxy1 = FakeProxy()
    proxy2 = FakeProxy()
    conductor = _make_conductor(proxy_chain=[proxy1, proxy2])
    assert conductor._is_terminal(0) is False
    assert conductor._is_terminal(1) is False
    assert conductor._is_terminal(2) is True


# ---------------------------------------------------------------------------
# _initialize_proxy tests (T9)
# ---------------------------------------------------------------------------


async def test_initialize_proxy_returns_intercepted_methods() -> None:
    """_initialize_proxy calls proxy.proxy_initialize() and returns methods."""
    proxy = FakeProxy(intercepted_methods=["session/prompt", "session/update"])
    conductor = _make_conductor()
    result = await conductor._initialize_proxy(proxy, 0)
    assert proxy.init_called
    assert result == ["session/prompt", "session/update"]


async def test_initialize_proxy_empty_intercepted_list() -> None:
    """_initialize_proxy returns empty list when proxy intercepts nothing."""
    proxy = FakeProxy(intercepted_methods=[])
    conductor = _make_conductor()
    result = await conductor._initialize_proxy(proxy, 0)
    assert result == []


# ---------------------------------------------------------------------------
# _initialize_chain tests (T9)
# ---------------------------------------------------------------------------


async def test_initialize_chain_zero_proxies() -> None:
    """_initialize_chain with no proxies skips proxy init, goes to terminal."""
    conductor = _make_conductor()
    conductor._connection = MagicMock()

    # Mock _initialize_terminal to avoid real ACP call
    conductor._initialize_terminal = AsyncMock()  # type: ignore[method-assign]

    await conductor._initialize_chain()

    assert conductor._chain_initialized is True
    assert conductor._intercepted_methods == []
    conductor._initialize_terminal.assert_called_once()


async def test_initialize_chain_n_proxies_in_order() -> None:
    """_initialize_chain calls proxy_initialize on each proxy in order."""
    proxy1 = FakeProxy(intercepted_methods=["session/prompt"])
    proxy2 = FakeProxy(intercepted_methods=["session/update"])
    conductor = _make_conductor(proxy_chain=[proxy1, proxy2])
    conductor._connection = MagicMock()
    conductor._initialize_terminal = AsyncMock()  # type: ignore[method-assign]

    await conductor._initialize_chain()

    assert proxy1.init_called
    assert proxy2.init_called
    assert conductor._intercepted_methods == [["session/prompt"], ["session/update"]]
    assert conductor._chain_initialized is True
    conductor._initialize_terminal.assert_called_once()


async def test_initialize_chain_proxy_crash_clears_state() -> None:
    """_initialize_chain clears intercepted_methods on proxy crash."""
    proxy1 = FakeProxy(intercepted_methods=["session/prompt"])
    proxy2 = FakeProxy(init_error=RuntimeError("init failed"))
    conductor = _make_conductor(proxy_chain=[proxy1, proxy2])
    conductor._connection = MagicMock()

    with pytest.raises(RuntimeError, match="init failed"):
        await conductor._initialize_chain()

    assert conductor._intercepted_methods == []
    assert not conductor._chain_initialized


async def test_initialize_chain_terminal_crash_clears_state() -> None:
    """_initialize_chain clears state when terminal init fails."""
    proxy = FakeProxy(intercepted_methods=["session/prompt"])
    conductor = _make_conductor(proxy_chain=[proxy])
    conductor._connection = MagicMock()
    conductor._initialize_terminal = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("terminal init failed"),
    )

    with pytest.raises(RuntimeError, match="terminal init failed"):
        await conductor._initialize_chain()

    assert conductor._intercepted_methods == []
    assert not conductor._chain_initialized


# ---------------------------------------------------------------------------
# _initialize_terminal tests (T9)
# ---------------------------------------------------------------------------


async def test_initialize_terminal_raises_without_connection() -> None:
    """_initialize_terminal raises RuntimeError when connection is None."""
    conductor = _make_conductor()
    with pytest.raises(RuntimeError, match="connection not established"):
        await conductor._initialize_terminal()


# ---------------------------------------------------------------------------
# _should_intercept tests (T10)
# ---------------------------------------------------------------------------


def test_should_intercept_true_for_registered_method() -> None:
    """_should_intercept returns True when a proxy intercepts the method."""
    proxy = FakeProxy(intercepted_methods=["session/prompt"])
    conductor = _setup_initialized_conductor(proxy_chain=[proxy])
    assert conductor._should_intercept("session/prompt") is True


def test_should_intercept_false_for_unregistered_method() -> None:
    """_should_intercept returns False when no proxy intercepts the method."""
    proxy = FakeProxy(intercepted_methods=["session/prompt"])
    conductor = _setup_initialized_conductor(proxy_chain=[proxy])
    assert conductor._should_intercept("session/update") is False


def test_should_intercept_false_when_no_proxies() -> None:
    """_should_intercept returns False when proxy chain is empty."""
    conductor = _setup_initialized_conductor()
    assert conductor._should_intercept("session/prompt") is False


def test_should_intercept_true_when_any_proxy_intercepts() -> None:
    """_should_intercept returns True if ANY proxy in the chain intercepts."""
    proxy1 = FakeProxy(intercepted_methods=["session/prompt"])
    proxy2 = FakeProxy(intercepted_methods=["session/update"])
    conductor = _setup_initialized_conductor(proxy_chain=[proxy1, proxy2])
    assert conductor._should_intercept("session/prompt") is True
    assert conductor._should_intercept("session/update") is True


# ---------------------------------------------------------------------------
# _handle_proxy_error tests (T10)
# ---------------------------------------------------------------------------


async def test_handle_proxy_error_generic_exception() -> None:
    """_handle_proxy_error produces JSON-RPC -32603 for generic exceptions."""
    conductor = _make_conductor()
    error = ValueError("something went wrong")
    result = await conductor._handle_proxy_error(error, 2)
    assert "error" in result
    error_obj = result["error"]
    assert error_obj["code"] == -32603
    assert "Proxy 2 error" in error_obj["message"]
    assert error_obj["data"]["proxyIndex"] == 2
    assert error_obj["data"]["errorType"] == "ValueError"


async def test_handle_proxy_error_request_error() -> None:
    """_handle_proxy_error uses RequestError's code and message."""
    conductor = _make_conductor()
    error = RequestError(-32001, "Custom error", {"detail": "blocked"})
    result = await conductor._handle_proxy_error(error, 0)
    assert "error" in result
    error_obj = result["error"]
    assert error_obj["code"] == -32001
    assert "Custom error" in str(error_obj["message"])
    assert error_obj["data"] == {"detail": "blocked"}


# ---------------------------------------------------------------------------
# _forward_through_proxies tests (T10)
# ---------------------------------------------------------------------------


async def test_forward_through_proxies_calls_intercepting_only() -> None:
    """_forward_through_proxies only calls proxies that intercept the method."""
    proxy1 = FakeProxy(
        intercepted_methods=["session/prompt"],
        successor_response={"result": "modified"},
    )
    proxy2 = FakeProxy(
        intercepted_methods=["session/update"],
        successor_response={"result": "ok"},
    )
    conductor = _setup_initialized_conductor(proxy_chain=[proxy1, proxy2])

    result = await conductor._forward_through_proxies(
        "session/prompt",
        {"prompt": []},
        {"direction": "forward"},
    )

    assert result == {"result": "modified"}
    assert len(proxy1.successor_calls) == 1
    assert len(proxy2.successor_calls) == 0


async def test_forward_through_proxies_no_interception_returns_original() -> None:
    """_forward_through_proxies returns original params when no proxy intercepts."""
    proxy = FakeProxy(intercepted_methods=["session/update"])
    conductor = _setup_initialized_conductor(proxy_chain=[proxy])

    original_params: dict[str, Any] = {"prompt": []}
    result = await conductor._forward_through_proxies(
        "session/prompt",
        original_params,
        {"direction": "forward"},
    )

    assert result is original_params
    assert len(proxy.successor_calls) == 0


async def test_forward_through_proxies_error_produces_jsonrpc_error() -> None:
    """_forward_through_proxies returns JSON-RPC error on proxy exception."""
    proxy = SuccessorFailingProxy(intercepted_methods=["session/prompt"])
    conductor = _setup_initialized_conductor(proxy_chain=[proxy])

    result = await conductor._forward_through_proxies(
        "session/prompt",
        {"prompt": []},
        {"direction": "forward"},
    )

    assert "error" in result
    assert result["error"]["code"] == -32603
    assert "successor failed" in result["error"]["message"]


async def test_forward_through_proxies_request_error_uses_own_code() -> None:
    """_forward_through_proxies uses RequestError's own code/message."""
    proxy = RequestErrorProxy(intercepted_methods=["session/prompt"])
    conductor = _setup_initialized_conductor(proxy_chain=[proxy])

    result = await conductor._forward_through_proxies(
        "session/prompt",
        {"prompt": []},
        {"direction": "forward"},
    )

    assert "error" in result
    assert result["error"]["code"] == -32001
    assert "Custom proxy error" in str(result["error"]["message"])


async def test_forward_through_proxies_multiple_intercepting() -> None:
    """_forward_through_proxies chains through multiple intercepting proxies."""
    proxy1 = FakeProxy(
        intercepted_methods=["session/prompt"],
        successor_response={"result": "first"},
    )
    proxy2 = FakeProxy(
        intercepted_methods=["session/prompt"],
        successor_response={"result": "second"},
    )
    conductor = _setup_initialized_conductor(proxy_chain=[proxy1, proxy2])

    result = await conductor._forward_through_proxies(
        "session/prompt",
        {"prompt": []},
        {"direction": "forward"},
    )

    # Last proxy's response wins
    assert result == {"result": "second"}
    assert len(proxy1.successor_calls) == 1
    assert len(proxy2.successor_calls) == 1


# ---------------------------------------------------------------------------
# _route_to_terminal tests (T10)
# ---------------------------------------------------------------------------


async def test_route_to_terminal_passthrough() -> None:
    """_route_to_terminal sends directly when no proxy intercepts (passthrough)."""
    mock_conn = MagicMock()
    mock_conn.send_request = AsyncMock(return_value={"result": "response"})
    conductor = _setup_initialized_conductor(
        proxy_chain=[FakeProxy(intercepted_methods=["session/update"])],
        connection=mock_conn,
    )

    result = await conductor._route_to_terminal("session/prompt", {"prompt": []})

    mock_conn.send_request.assert_called_once_with("session/prompt", {"prompt": []})
    assert result == {"result": "response"}


async def test_route_to_terminal_with_interception() -> None:
    """_route_to_terminal forwards through proxies when they intercept."""
    mock_conn = MagicMock()
    mock_conn.send_request = AsyncMock(return_value={"result": "terminal_response"})
    proxy = FakeProxy(
        intercepted_methods=["session/prompt"],
        successor_response={"prompt": [{"type": "text", "text": "modified"}]},
    )
    conductor = _setup_initialized_conductor(
        proxy_chain=[proxy],
        connection=mock_conn,
    )

    result = await conductor._route_to_terminal("session/prompt", {"prompt": []})

    # Proxy modified params, then sent to terminal
    assert len(proxy.successor_calls) == 1
    mock_conn.send_request.assert_called_once()
    sent_params = mock_conn.send_request.call_args[0][1]
    assert sent_params == {"prompt": [{"type": "text", "text": "modified"}]}
    assert result == {"result": "terminal_response"}


async def test_route_to_terminal_proxy_error_stops_propagation() -> None:
    """_route_to_terminal stops when proxy returns error response."""
    mock_conn = MagicMock()
    mock_conn.send_request = AsyncMock()
    proxy = SuccessorFailingProxy(intercepted_methods=["session/prompt"])
    conductor = _setup_initialized_conductor(
        proxy_chain=[proxy],
        connection=mock_conn,
    )

    result = await conductor._route_to_terminal("session/prompt", {"prompt": []})

    assert "error" in result
    mock_conn.send_request.assert_not_called()


async def test_route_to_terminal_raises_without_connection() -> None:
    """_route_to_terminal raises RuntimeError when connection is None."""
    conductor = _make_conductor()
    with pytest.raises(RuntimeError, match="connection not established"):
        await conductor._route_to_terminal("session/prompt", {})


async def test_route_to_terminal_non_dict_response_wrapped() -> None:
    """_route_to_terminal wraps non-dict response in {"result": ...}."""
    mock_conn = MagicMock()
    mock_conn.send_request = AsyncMock(return_value="plain_string")
    conductor = _setup_initialized_conductor(connection=mock_conn)

    result = await conductor._route_to_terminal("session/prompt", {"prompt": []})

    assert result == {"result": "plain_string"}


# ---------------------------------------------------------------------------
# _route_message tests (T10)
# ---------------------------------------------------------------------------


async def test_route_message_forward_direction() -> None:
    """_route_message with direction=forward calls _route_to_terminal."""
    mock_conn = MagicMock()
    mock_conn.send_request = AsyncMock(return_value={"result": "ok"})
    conductor = _setup_initialized_conductor(connection=mock_conn)

    result = await conductor._route_message(
        "session/prompt",
        {"prompt": []},
        {"direction": "forward"},
    )

    assert result == {"result": "ok"}
    mock_conn.send_request.assert_called_once()


async def test_route_message_default_direction_is_forward() -> None:
    """_route_message defaults to forward when direction is not specified."""
    mock_conn = MagicMock()
    mock_conn.send_request = AsyncMock(return_value={"result": "ok"})
    conductor = _setup_initialized_conductor(connection=mock_conn)

    result = await conductor._route_message(
        "session/prompt",
        {"prompt": []},
        {},
    )

    assert result == {"result": "ok"}


async def test_route_message_reverse_direction_returns_params() -> None:
    """_route_message with direction=reverse returns params directly (stub)."""
    conductor = _setup_initialized_conductor()

    params = {"result": "reverse_response"}
    result = await conductor._route_message(
        "session/prompt",
        params,
        {"direction": "reverse"},
    )

    assert result == params


# ---------------------------------------------------------------------------
# _step property tests (T11)
# ---------------------------------------------------------------------------


def test_step_returns_step_with_conductor_name() -> None:
    """_step property returns a Step with the Conductor's name as ID."""
    conductor = _make_conductor()
    step = conductor._step
    assert step is not None
    assert step.label == "Conductor(test_conductor)"


# ---------------------------------------------------------------------------
# __aexit__ tests
# ---------------------------------------------------------------------------


async def test_aexit_clears_state() -> None:
    """__aexit__ clears runtime state even without full init."""
    conductor = _make_conductor()
    # Simulate partial initialization
    conductor._intercepted_methods = [["session/prompt"]]
    conductor._chain_initialized = True
    conductor._conductor_initialized = True
    conductor._exit_stack = None  # Avoid real cleanup

    await conductor.__aexit__(None, None, None)

    assert conductor._intercepted_methods == []
    assert not conductor._chain_initialized
    assert not conductor._conductor_initialized
    assert conductor._connection is None
    assert conductor._process is None


async def test_aexit_cleans_up_exit_stack() -> None:
    """__aexit__ closes the exit stack if it exists."""
    conductor = _make_conductor()
    mock_stack = MagicMock()
    mock_stack.aclose = AsyncMock()
    conductor._exit_stack = mock_stack
    conductor._conductor_initialized = True

    await conductor.__aexit__(None, None, None)

    mock_stack.aclose.assert_called_once()
    assert conductor._exit_stack is None


async def test_aexit_cleans_up_owned_handler() -> None:
    """__aexit__ cleans up handler when Conductor owns it."""
    mock_handler = MagicMock()
    mock_handler.cleanup = AsyncMock()
    conductor = Conductor(
        name="test",
        command="echo",
        client_handler=mock_handler,
    )
    conductor._exit_stack = None
    conductor._conductor_initialized = True
    # Force ownership flag
    conductor._owns_handler = True

    await conductor.__aexit__(None, None, None)

    mock_handler.cleanup.assert_called_once()


# ---------------------------------------------------------------------------
# get_stats tests (T11)
# ---------------------------------------------------------------------------


async def test_get_stats_empty_connections() -> None:
    """get_stats returns empty MessageStats when no connections exist."""
    conductor = _make_conductor()
    stats = await conductor.get_stats()
    # Should return a MessageStats (empty)
    assert stats is not None


# ---------------------------------------------------------------------------
# Properties tests
# ---------------------------------------------------------------------------


def test_config_property_returns_conductor_config() -> None:
    """Config property returns the ConductorConfig."""
    conductor = Conductor(name="test", command="goose", args=["acp"])
    assert isinstance(conductor.config, ConductorConfig)
    assert conductor.config.command == "goose"


def test_proxy_chain_property_returns_list() -> None:
    """proxy_chain property returns the proxy list."""
    proxy = FakeProxy()
    conductor = Conductor(name="test", command="echo", proxy_chain=[proxy])
    assert conductor.proxy_chain == [proxy]


def test_connection_property_returns_none_before_init() -> None:
    """Connection property returns None before __aenter__."""
    conductor = _make_conductor()
    assert conductor.connection is None


def test_is_initialized_property() -> None:
    """is_initialized reflects _conductor_initialized state."""
    conductor = _make_conductor()
    assert not conductor.is_initialized
    conductor._conductor_initialized = True
    assert conductor.is_initialized


# ---------------------------------------------------------------------------
# FakeProxy implements Proxy protocol
# ---------------------------------------------------------------------------


def test_fake_proxy_is_proxy() -> None:
    """FakeProxy implements the Proxy protocol."""
    proxy = FakeProxy()
    assert isinstance(proxy, Proxy)


def test_successor_failing_proxy_is_proxy() -> None:
    """SuccessorFailingProxy implements the Proxy protocol."""
    proxy = SuccessorFailingProxy()
    assert isinstance(proxy, Proxy)


def test_request_error_proxy_is_proxy() -> None:
    """RequestErrorProxy implements the Proxy protocol."""
    proxy = RequestErrorProxy()
    assert isinstance(proxy, Proxy)
