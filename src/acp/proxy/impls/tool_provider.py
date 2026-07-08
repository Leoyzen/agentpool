"""ToolProviderProxy — experimental proxy for MCP-over-ACP tool sharing.

This is an experimental implementation that will eventually use
AcpMcpTransport/AcpMcpConnectionManager to share tools across ACP agents.
Currently a stub that passes messages through unchanged.
"""

from __future__ import annotations

from typing import Any

from agentpool.log import get_logger


logger = get_logger(__name__)


class ToolProviderProxy:
    """Experimental proxy for providing tools via MCP-over-ACP.

    Implements the Proxy protocol defined in acp.proxy.protocol.

    NOTE: This is experimental. Future implementation will use
    AcpMcpTransport and AcpMcpConnectionManager for real MCP tool sharing.
    """

    def __init__(self) -> None:
        """Initialize the ToolProviderProxy."""
        logger.debug("ToolProviderProxy initialized (experimental)")

    def proxy_initialize(self) -> list[str]:
        """Return the list of ACP methods this proxy intercepts.

        Returns:
            List of intercepted method names.
        """
        return ["session/prompt"]

    async def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Pass through messages unchanged (experimental stub).

        Future implementation will intercept session/prompt to advertise
        available MCP tools to the terminal agent.

        Args:
            method: The ACP method name.
            params: The method parameters.
            meta: Message metadata.

        Returns:
            Unchanged params (passthrough).
        """
        return params
