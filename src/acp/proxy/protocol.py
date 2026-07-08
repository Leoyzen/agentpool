"""Proxy protocol for ACP proxy chain."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class Proxy(Protocol):
    """Protocol for ACP proxy chain components.

    A proxy intercepts messages between the client and terminal agent.
    Each proxy declares which methods it intercepts and handles
    successor message forwarding.
    """

    def proxy_initialize(self) -> list[str]:
        """Initialize the proxy and return intercepted method names.

        Returns:
            List of method names this proxy intercepts.
        """
        ...

    def proxy_successor(
        self,
        method: str,
        params: dict[str, Any],
        meta: dict[str, Any],
    ) -> dict[str, Any]:
        """Forward a successor message to the next component in the chain.

        Args:
            method: The JSON-RPC method name.
            params: The method parameters.
            meta: Additional metadata for routing.

        Returns:
            The response from the successor.
        """
        ...
