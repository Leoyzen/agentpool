"""ACP Conductor — manages proxy chain lifecycle and terminal agent subprocess.

The Conductor inherits from :class:`MessageNode` and owns the
:class:`ACPClientHandler`. It is responsible for spawning the terminal agent
subprocess, wiring JSON-RPC connections, and managing the ACP client handler
lifecycle. Message routing (T9), passthrough optimization (T10), and complete
``_step`` implementation (T11) are added incrementally.

Design references:
- D1: Conductor inherits ``MessageNode[ChatMessage, ChatMessage[str]]``
- D5: Passthrough optimization — skip deserialization for unregistered methods
- D8: Conductor owns ``ACPClientHandler`` (transferred from ``ACPAgent``)
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, override

import structlog

from acp.exceptions import RequestError
from acp.proxy.constants import PROXY_INITIALIZE, PROXY_SUCCESSOR
from agentpool.messaging.messagenode import MessageNode


logger: structlog.stdlib.BoundLogger = structlog.get_logger(__name__)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping
    from pathlib import Path
    from types import TracebackType

    from anyio.abc import ByteReceiveStream, ByteSendStream, Process
    from pydantic_graph import Step

    from acp.client.connection import ClientSideConnection
    from acp.proxy.protocol import Proxy
    from agentpool.agents.acp_agent.client_handler import ACPClientHandler
    from agentpool.hooks.agent_hooks import AgentHooks
    from agentpool.messaging import ChatMessage
    from agentpool.talk.stats import AggregatedMessageStats, MessageStats


@dataclass
class ConductorConfig:
    """Configuration for spawning the terminal agent subprocess.

    Attributes:
        command: Shell command to execute.
        args: Arguments for the command.
        env: Environment variables for the subprocess.
        cwd: Working directory for the subprocess.
    """

    command: str
    """Shell command to execute."""
    args: list[str] = field(default_factory=list)
    """Arguments for the command."""
    env: Mapping[str, str] | None = None
    """Environment variables for the subprocess."""
    cwd: str | Path | None = None
    """Working directory for the subprocess."""


class Conductor(MessageNode[Any, str]):
    """Manages proxy chain lifecycle and terminal agent subprocess.

    The Conductor spawns the terminal ACP agent subprocess using anyio task
    groups for structured concurrency, wires the JSON-RPC connection to
    ``ClientSideConnection``, and owns the :class:`ACPClientHandler`
    lifecycle.

    !!! note "Task scope"

        T8: class structure + handler ownership.
        T9: chain initialization (``_initialize_chain``).
        T10: message routing, passthrough, error propagation.
        T11: complete ``_step`` implementation (pending).
    """

    def __init__(
        self,
        name: str,
        command: str,
        args: list[str] | None = None,
        *,
        cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        proxy_chain: list[Proxy] | None = None,
        client_handler: ACPClientHandler | None = None,
        agent_hooks: AgentHooks | None = None,
        description: str | None = None,
        **kwargs: Any,
    ) -> None:
        """Initialize the Conductor.

        Args:
            name: Conductor name (used as node identity).
            command: Subprocess command to spawn the terminal agent.
            args: Arguments for the command.
            cwd: Working directory for the subprocess.
            env: Environment variables for the subprocess.
            proxy_chain: Optional list of proxies in the chain.
                The last component is the terminal agent; all others are
                proxies. When ``None`` or empty, the Conductor connects
                directly to the terminal agent.
            client_handler: Optional pre-created handler. When ``None``,
                the Conductor will own the handler lifecycle but defer
                creation until sufficient context is available (T13).
            agent_hooks: Optional AgentHooks from the agent. When hooks are
                present and no HookProxy is in the chain, a HookProxy is
                auto-inserted at position 0.
            description: Optional human-readable description.
            **kwargs: Additional keyword arguments passed to MessageNode.
        """
        super().__init__(name=name, description=description, **kwargs)

        self._config = ConductorConfig(
            command=command,
            args=list(args) if args else [],
            env=env,
            cwd=cwd,
        )
        self._proxy_chain: list[Proxy] = list(proxy_chain) if proxy_chain else []
        self._client_handler: ACPClientHandler | None = client_handler
        self._owns_handler: bool = client_handler is None
        self._agent_hooks: AgentHooks | None = agent_hooks
        self._has_hook_proxy: bool = False

        # Runtime state — populated during __aenter__
        self._process: Process | None = None
        self._reader: ByteReceiveStream | None = None
        self._writer: ByteSendStream | None = None
        self._connection: ClientSideConnection | None = None
        self._exit_stack: contextlib.AsyncExitStack | None = None
        self._conductor_initialized: bool = False

        # Chain initialization state — populated during _initialize_chain
        self._intercepted_methods: list[list[str]] = []
        self._chain_initialized: bool = False

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def config(self) -> ConductorConfig:
        """Get the subprocess configuration."""
        return self._config

    @property
    def proxy_chain(self) -> list[Proxy]:
        """Get the proxy chain (may be empty)."""
        return self._proxy_chain

    @property
    def client_handler(self) -> ACPClientHandler | None:
        """Get the owned ACPClientHandler, if initialized."""
        return self._client_handler

    @property
    def connection(self) -> ClientSideConnection | None:
        """Get the client-side connection to the terminal agent."""
        return self._connection

    @property
    def process(self) -> Process | None:
        """Get the terminal agent subprocess, if spawned."""
        return self._process

    @property
    def is_initialized(self) -> bool:
        """Whether the Conductor has been entered via ``__aenter__``."""
        return self._conductor_initialized

    @property
    def has_hook_proxy(self) -> bool:
        """Whether a HookProxy is active in the proxy chain."""
        return self._has_hook_proxy

    def get_turn_hooks(self) -> AgentHooks | None:
        """Return hooks for ACPTurn, or None if HookProxy handles them.

        When a HookProxy is in the chain, returns None so HookAwareTurn
        skips hook firing (hooks are handled by the proxy). Otherwise,
        returns the agent's AgentHooks for normal HookAwareTurn firing.

        Returns:
            AgentHooks if no HookProxy, None if HookProxy is active.
        """
        if self._has_hook_proxy:
            return None
        return self._agent_hooks

    def _maybe_auto_insert_hook_proxy(self) -> None:
        """Auto-insert HookProxy at position 0 when agent has hooks.

        If the agent has hooks (AgentHooks with has_hooks() == True) and
        no HookProxy is already in the chain, creates a HookProxy wrapping
        the hooks and inserts it at position 0.
        """
        if self._agent_hooks is None or not self._agent_hooks.has_hooks():
            return

        # Check if HookProxy is already in the chain
        from acp.proxy.impls.hook_proxy import HookProxy

        for proxy in self._proxy_chain:
            if isinstance(proxy, HookProxy):
                self._has_hook_proxy = True
                return

        # Auto-insert HookProxy at position 0
        hook_proxy = HookProxy(hooks=[self._agent_hooks])
        self._proxy_chain.insert(0, hook_proxy)
        self._has_hook_proxy = True

    def _detect_hook_proxy(self) -> None:
        """Detect if a HookProxy is in the chain after initialization."""
        from acp.proxy.impls.hook_proxy import HookProxy

        self._has_hook_proxy = any(
            isinstance(proxy, HookProxy) for proxy in self._proxy_chain
        )

    @override
    @property
    def agent_type(self) -> str:
        """Return the agent-type string for persistence."""
        return "acp"

    # ------------------------------------------------------------------
    # Lifecycle (async context manager)
    # ------------------------------------------------------------------

    @override
    async def __aenter__(self) -> Self:
        """Start the terminal agent subprocess and initialize the chain.

        Spawns the subprocess using anyio task groups for structured
        concurrency, wires the JSON-RPC connection, and creates/initializes
        the :class:`ACPClientHandler` if not pre-provided.
        """
        await super().__aenter__()

        from acp.client.connection import ClientSideConnection
        from acp.client.implementations import NoOpClient
        from acp.transports import spawn_stdio_transport

        self._exit_stack = contextlib.AsyncExitStack()

        # Spawn the terminal agent subprocess using anyio structured concurrency.
        # spawn_stdio_transport uses anyio internally for process management.
        transport_ctx = spawn_stdio_transport(
            self._config.command,
            *self._config.args,
            env=self._config.env,
            cwd=self._config.cwd,
        )
        reader, writer, process = await self._exit_stack.enter_async_context(
            transport_ctx,
        )
        self._reader = reader
        self._writer = writer
        self._process = process

        # Wire the subprocess JSON-RPC connection to ClientSideConnection.
        # ClientSideConnection handles notifications from the terminal agent.
        def client_factory(agent: Any) -> NoOpClient:
            return NoOpClient()

        self._connection = ClientSideConnection(client_factory, writer, reader)
        self._exit_stack.push_async_callback(self._connection.close)

        # Create ACPClientHandler if not pre-provided.
        # Full handler initialization requires ACPAgent/ACPState context
        # which will be wired in T13 (ACPAgent refactor). For now, the
        # handler is owned but not fully initialized — this matches the
        # task scope (T8: class structure + handler ownership).
        if self._client_handler is None and self._owns_handler:
            # ACPClientHandler requires an ACPAgent and ACPState at
            # construction time. The Conductor will create the handler
            # when it has the necessary context (T13 wires this).
            # For T8, we store None and allow external injection.
            pass

        # Auto-insert HookProxy if agent has hooks and none is in the chain.
        self._maybe_auto_insert_hook_proxy()

        # Initialize the proxy chain: call proxy/initialize on each
        # proxy, then initialize on the terminal agent. If any
        # component fails, clean up all started components.
        try:
            await self._initialize_chain()
        except Exception:
            if self._exit_stack is not None:
                await self._exit_stack.aclose()
                self._exit_stack = None
            self._process = None
            self._reader = None
            self._writer = None
            self._connection = None
            raise

        # Disable request_permission hooks when HookProxy is active
        # to prevent double-firing (hooks handled by proxy, not handler).
        if self._has_hook_proxy and self._client_handler is not None:
            self._client_handler.set_hooks_enabled(False)

        self._conductor_initialized = True
        return self

    @override
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up subprocess and all connections.

        Ensures no orphaned subprocesses remain. Cleanup runs in a
        ``finally``-like manner via the exit stack.
        """
        # Clean up handler if we own it
        if self._client_handler is not None and self._owns_handler:
            with contextlib.suppress(Exception):
                await self._client_handler.cleanup()

        if self._exit_stack is not None:
            await self._exit_stack.aclose()
            self._exit_stack = None

        self._process = None
        self._reader = None
        self._writer = None
        self._connection = None
        self._intercepted_methods.clear()
        self._chain_initialized = False
        self._conductor_initialized = False

        await super().__aexit__(exc_type, exc_val, exc_tb)

    # ------------------------------------------------------------------
    # Chain initialization (T9)
    # ------------------------------------------------------------------

    def _is_terminal(self, index: int) -> bool:
        """Return True if the component at *index* is the terminal agent.

        The terminal agent is the last component in the chain. Since
        ``_proxy_chain`` contains only proxies, any index equal to or
        greater than its length refers to the terminal agent position.

        Args:
            index: Zero-based chain position (0 = first proxy).

        Returns:
            True if the index refers to the terminal agent.
        """
        return index >= len(self._proxy_chain)

    async def _initialize_chain(self) -> None:
        """Run the full proxy chain initialization sequence.

        Calls ``proxy/initialize`` ({attr:`PROXY_INITIALIZE`}) on each
        proxy in order from client toward terminal agent, then calls
        ``initialize`` on the terminal agent (last component).

        After initialization, the intercepted-methods lists from each
        proxy are stored for use by message routing (T10). The
        ``proxy/successor`` ({attr:`PROXY_SUCCESSOR`}) forwarding chain
        is established implicitly by the list ordering: proxy *i*'s
        successor is proxy *i+1*, and the last proxy's successor is the
        terminal agent.

        !!! note "Zero-proxy case"

            When ``_proxy_chain`` is empty, this method skips proxy
            initialization and connects directly to the terminal agent.

        Raises:
            Exception: If any proxy or the terminal agent fails during
                initialization. All started components are cleaned up
                before re-raising.
        """
        # Initialize each proxy in order (client → terminal).
        for i, proxy in enumerate(self._proxy_chain):
            try:
                intercepted = await self._initialize_proxy(proxy, i)
            except Exception:
                # A proxy crashed during init — abort and clean up.
                logger.exception(
                    "proxy_init_failed",
                    proxy_index=i,
                    method=PROXY_INITIALIZE,
                )
                self._intercepted_methods.clear()
                raise
            self._intercepted_methods.append(intercepted)

        # Initialize the terminal agent (last component).
        try:
            await self._initialize_terminal()
        except Exception:
            # Terminal agent init failed — clean up proxy state.
            logger.exception("terminal_init_failed")
            self._intercepted_methods.clear()
            raise

        self._chain_initialized = True
        logger.info(
            "chain_initialized",
            proxy_count=len(self._proxy_chain),
            forwarding_method=PROXY_SUCCESSOR,
        )

    async def _initialize_proxy(self, proxy: Proxy, index: int) -> list[str]:
        """Initialize a single proxy and return its intercepted methods.

        Calls ``proxy_initialize()`` on the proxy, which returns the
        list of ACP method names the proxy intercepts. These are stored
        by the Conductor for passthrough optimization (T10): message
        types not in any proxy's ``intercepted_methods`` are forwarded
        without deserialization.

        Args:
            proxy: The proxy to initialize.
            index: Zero-based chain position (0 = closest to client).

        Returns:
            List of intercepted ACP method names (e.g.
            ``["session/prompt", "session/update"]``).
        """
        logger.debug("proxy_init_start", proxy_index=index, method=PROXY_INITIALIZE)
        return proxy.proxy_initialize()

    async def _initialize_terminal(self) -> None:
        """Initialize the terminal agent (last component in the chain).

        Sends the standard ACP ``initialize`` method to the terminal
        agent subprocess via the :class:`ClientSideConnection`. This is
        NOT ``proxy/initialize`` — the terminal agent is a standard ACP
        agent and does not know about proxy chains.

        Raises:
            RuntimeError: If the connection has not been established.
        """
        if self._connection is None:
            raise RuntimeError(
                "Cannot initialize terminal agent: connection not established",
            )

        from acp.agent.acp_agent_api import ACPAgentAPI

        api = ACPAgentAPI(self._connection)
        await api.initialize(
            title=self.name,
            version="0.1.0",
            name=self.name,
        )

    # ------------------------------------------------------------------
    # Message routing (T10)
    # ------------------------------------------------------------------

    def _should_intercept(self, method: str) -> bool:
        """Check if any proxy in the chain intercepts the given method.

        Uses the ``intercepted_methods`` lists collected during
        :meth:`_initialize_chain` to determine whether any proxy
        declared interest in this method. When no proxy intercepts a
        method, the Conductor can forward the raw message directly to
        the terminal agent without deserialization (passthrough
        optimization, design D5).

        Args:
            method: JSON-RPC method name (e.g. ``"session/prompt"``).

        Returns:
            True if at least one proxy intercepts this method.
        """
        return any(
            method in intercepted for intercepted in self._intercepted_methods
        )

    async def _forward_through_proxies(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Forward a message through each proxy that intercepts the method.

        Iterates through the proxy chain in order (client → terminal).
        For each proxy whose ``intercepted_methods`` list contains the
        given *method*, calls ``proxy_successor()`` to let the proxy
        inspect, modify, or block the message before forwarding.

        Proxies that do not intercept the method are skipped (they
        would forward without deserialization anyway, so the
        Conductor short-circuits them).

        If a proxy raises an exception, the error is propagated as a
        JSON-RPC error response — never silently skipped.

        Args:
            method: JSON-RPC method name.
            params: Method parameters (may be modified by proxies).
            meta: Additional metadata for routing (e.g. request ID,
                session ID, chain position).

        Returns:
            The response from the last intercepting proxy, or the
            original *params* if no proxy intercepted the method.
        """
        result: dict[str, Any] = params
        for i, proxy in enumerate(self._proxy_chain):
            if method not in self._intercepted_methods[i]:
                continue
            try:
                result = await proxy.proxy_successor(method, result, meta)
            except Exception as exc:
                logger.exception(
                    "proxy_forward_failed",
                    proxy_index=i,
                    method=method,
                )
                return await self._handle_proxy_error(exc, i)
        return result

    async def _handle_proxy_error(
        self,
        error: Exception,
        proxy_index: int,
    ) -> dict[str, Any]:
        """Produce a JSON-RPC error response for a proxy exception.

        Per the spec, proxy exceptions MUST produce a JSON-RPC error
        response forwarded back through the chain. The Conductor SHALL
        NOT silently skip failed proxies — a security hook proxy
        failing silently is dangerous.

        If the exception is already a :class:`RequestError`, its code
        and message are used directly. Otherwise, an internal error
        code (-32603) is used with the exception message.

        Args:
            error: The exception raised by the proxy.
            proxy_index: Zero-based index of the failed proxy.

        Returns:
            A dict with ``"error"`` key containing ``code``,
            ``message``, and ``data`` fields following JSON-RPC 2.0
            error object format.
        """
        if isinstance(error, RequestError):
            error_obj: dict[str, Any] = {
                "code": error.code,
                "message": str(error),
                "data": error.data,
            }
        else:
            error_obj = {
                "code": -32603,
                "message": f"Proxy {proxy_index} error: {error}",
                "data": {
                    "proxyIndex": proxy_index,
                    "errorType": type(error).__name__,
                },
            }
        logger.error(
            "proxy_error_propagated",
            proxy_index=proxy_index,
            error_code=error_obj["code"],
            error_message=error_obj["message"],
        )
        return {"error": error_obj}

    async def _route_to_terminal(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a message through the proxy chain to the terminal agent.

        This is the core forwarding path for client→terminal messages.
        It:

        1. Builds routing metadata (chain position, method name).
        2. If any proxy intercepts *method*, forwards through each
           intercepting proxy via :meth:`_forward_through_proxies`.
           If a proxy returns an error response, propagation stops
           immediately and the error is returned.
        3. Sends the (possibly modified) message to the terminal agent
           via :class:`ClientSideConnection` using
           ``send_request()``.
        4. Returns the terminal agent's response.

        For passthrough (no proxy intercepts *method*), the raw
        message is sent directly to the terminal agent without any
        proxy processing — the deserialization cost is zero (D5).

        Args:
            method: JSON-RPC method name (e.g. ``"session/prompt"``).
            params: Method parameters.

        Returns:
            The response dict from the terminal agent, or an error
            dict if a proxy blocked the message.

        Raises:
            RuntimeError: If the connection has not been established.
        """
        if self._connection is None:
            raise RuntimeError(
                "Cannot route message: connection not established",
            )

        meta: dict[str, Any] = {
            "method": method,
            "chain_length": len(self._proxy_chain),
        }

        # If any proxy intercepts this method, forward through
        # the proxy chain first. Proxies may modify params or
        # block the message entirely.
        if self._should_intercept(method):
            proxy_result = await self._forward_through_proxies(
                method,
                params,
                meta,
            )
            # If a proxy returned an error response, stop
            # propagation — do not forward to terminal agent.
            if "error" in proxy_result:
                return proxy_result
            params = proxy_result

        # Send to terminal agent via the wire connection.
        response = await self._connection.send_request(method, params)
        if isinstance(response, dict):
            return response
        return {"result": response}

    async def _route_message(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Route a message bidirectionally through the proxy chain.

        This is the main entry point for message routing. It handles
        both forward (client → terminal) and reverse (terminal →
        client, i.e. response) routing.

        For forward routing, the message flows through each
        intercepting proxy and then to the terminal agent. For
        reverse routing (responses flowing back), the message flows
        through intercepting proxies in reverse order.

        !!! note "Passthrough optimization"

            When no proxy intercepts *method*, the message is
            forwarded directly to the terminal agent without
            deserialization (design D5).

        Args:
            method: JSON-RPC method name.
            params: Method parameters.
            meta: Additional metadata for routing (e.g. direction,
                request ID for response correlation).

        Returns:
            The response dict from the terminal agent or from
            intercepting proxies.
        """
        direction = meta.get("direction", "forward")
        if direction == "forward":
            return await self._route_to_terminal(method, params)
        # Reverse direction: responses flowing back from terminal
        # agent through proxies to the client. Currently, responses
        # are returned directly by _route_to_terminal. Full reverse
        # proxy routing will be implemented when proxy response
        # interception is needed (e.g. HookProxy post_turn).
        if isinstance(params, dict):
            return params
        return {"result": params}

    # ------------------------------------------------------------------
    # MessageNode abstract methods (T11)
    # ------------------------------------------------------------------

    @override
    async def get_stats(self) -> MessageStats | AggregatedMessageStats:
        """Get message statistics for this node.

        Returns connection stats aggregated from all active Talk
        connections. When the Conductor has no connections, returns an
        empty :class:`MessageStats`.

        Returns:
            Aggregated stats from all connections, or a fresh
            :class:`MessageStats` if no connections exist.
        """
        from agentpool.talk.stats import AggregatedMessageStats, MessageStats

        talks = self.connections.get_connections()
        if not talks:
            return MessageStats()
        return AggregatedMessageStats(stats=[talk.stats for talk in talks])

    @override
    def run_iter(self, *prompts: Any, **kwargs: Any) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages during sequential execution of multiple prompts.

        Each prompt is routed through the proxy chain to the terminal
        agent, and the response is yielded as a :class:`ChatMessage`.

        Args:
            *prompts: Input prompts to process sequentially.
            **kwargs: Additional execution arguments.

        Yields:
            Response :class:`ChatMessage` from the terminal agent for
            each prompt, in order.
        """
        return self._run_iter_impl(*prompts, **kwargs)

    async def _run_iter_impl(
        self,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[ChatMessage[Any]]:
        """Implementation of :meth:`run_iter`.

        Args:
            *prompts: Input prompts to process sequentially.
            **kwargs: Additional execution arguments.

        Yields:
            Response :class:`ChatMessage` from the terminal agent for
            each prompt.
        """
        for prompt in prompts:
            result = await self.run(prompt, **kwargs)
            yield result

    @property
    def _step(self) -> Step:
        """Return a pydantic-graph Step wrapping the Conductor's execution.

        The Step's ``call`` function receives a :class:`StepContext`
        containing :class:`AgentPoolState` with the input prompts. It
        routes the prompt through the proxy chain via
        :meth:`_route_message` and returns a :class:`ChatMessage[str]`
        with the terminal agent's response.

        Returns:
            A pydantic-graph :class:`Step` configured with the
            Conductor's execution logic.
        """
        from pydantic_graph import Step
        from pydantic_graph.id_types import NodeID

        return Step(
            id=NodeID(self.name),
            call=self._execute_step,
            label=f"Conductor({self.name})",
        )

    async def _execute_step(self, ctx: Any) -> ChatMessage[str]:
        """Step function that routes a prompt through the proxy chain.

        Extracts the input prompt from the :class:`StepContext` state,
        routes it through the proxy chain to the terminal agent via
        :meth:`_route_message`, and returns the response as a
        :class:`ChatMessage[str]`.

        Args:
            ctx: pydantic-graph :class:`StepContext` containing
                :class:`AgentPoolState` with prompts and kwargs.

        Returns:
            A :class:`ChatMessage[str]` containing the terminal agent's
            response.

        Raises:
            RuntimeError: If the Conductor has not been initialized
                (``__aenter__`` not called) or the connection is not
                established.
        """
        from agentpool.messaging import ChatMessage

        state: Any = ctx.state
        prompts: tuple[Any, ...] = state.prompts

        if not self._conductor_initialized:
            raise RuntimeError(
                "Conductor must be entered via __aenter__ before execution",
            )

        if self._connection is None:
            raise RuntimeError(
                "Cannot execute step: connection not established",
            )

        # Build session/prompt params from the input prompts.
        # The Conductor routes JSON-RPC messages; the first prompt
        # is treated as the user's text input.
        prompt_text: str = ""
        if prompts:
            first = prompts[0]
            prompt_text = first if isinstance(first, str) else str(first)

        params: dict[str, Any] = {
            "prompt": [{"type": "text", "text": prompt_text}],
        }

        meta: dict[str, Any] = {"direction": "forward"}
        response = await self._route_message("session/prompt", params, meta)

        # Extract text content from the response.
        result_text: str = ""
        if "result" in response:
            result_val = response["result"]
            if isinstance(result_val, str):
                result_text = result_val
            elif isinstance(result_val, dict):
                result_text = str(result_val.get("text", result_val))
            else:
                result_text = str(result_val)
        elif "error" in response:
            error_obj = response["error"]
            result_text = f"Error: {error_obj.get('message', 'Unknown error')}"

        # Store the result on the state for run_stream() to pick up.
        result_message: ChatMessage[str] = ChatMessage(
            content=result_text,
            role="assistant",
            name=self.name,
        )
        state.result = result_message
        return result_message

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return a debug representation."""
        status = "initialized" if self._conductor_initialized else "not initialized"
        return f"Conductor(name={self.name!r}, command={self._config.command!r}, {status})"
