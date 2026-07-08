"""ACP Conductor — manages proxy chain lifecycle and terminal agent subprocess.

The Conductor inherits from :class:`MessageNode` and owns the
:class:`ACPClientHandler`. It is responsible for spawning the terminal agent
subprocess, wiring JSON-RPC connections, and managing the ACP client handler
lifecycle. Full message routing (T9), passthrough (T10), and complete ``_step``
implementation (T11) will be added in subsequent tasks.

Design references:
- D1: Conductor inherits ``MessageNode[ChatMessage, ChatMessage[str]]``
- D8: Conductor owns ``ACPClientHandler`` (transferred from ``ACPAgent``)
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self, override

import structlog

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

        This is the Phase 2 implementation (T8). Full message routing (T9),
        passthrough optimization (T10), and complete ``_step`` (T11) will be
        added in subsequent tasks.
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
    # MessageNode abstract methods (T9, T10, T11 will implement fully)
    # ------------------------------------------------------------------

    @override
    async def get_stats(self) -> MessageStats | AggregatedMessageStats:
        """Get message statistics for this node.

        !!! note "Not yet implemented"

            Full implementation deferred to T11.
        """
        raise NotImplementedError(
            "Conductor.get_stats() will be implemented in T11",
        )

    @override
    def run_iter(self, *prompts: Any, **kwargs: Any) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages during execution.

        !!! note "Not yet implemented"

            Full implementation deferred to T11.
        """
        raise NotImplementedError(
            "Conductor.run_iter() will be implemented in T11",
        )

    @property
    def _step(self) -> Step:
        """Return a pydantic-graph Step wrapping the Conductor's execution.

        !!! note "Minimal implementation"

            Full message routing through the proxy chain will be added
            in T9 (routing) and T11 (complete ``_step``). This minimal
            Step delegates to :meth:`_execute_step` which is a stub.
        """
        from pydantic_graph import Step
        from pydantic_graph.id_types import NodeID

        return Step(
            id=NodeID(self.name),
            call=self._execute_step,
            label=f"Conductor({self.name})",
        )

    async def _execute_step(self, ctx: Any) -> ChatMessage[str]:
        """Step function that runs the Conductor's execution.

        !!! note "Not yet implemented"

            This is a minimal stub. Full implementation with proxy chain
            routing will be added in T9/T11.
        """
        raise NotImplementedError(
            "Conductor._execute_step() will be implemented in T9/T11",
        )

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        """Return a debug representation."""
        status = "initialized" if self._conductor_initialized else "not initialized"
        return f"Conductor(name={self.name!r}, command={self._config.command!r}, {status})"
