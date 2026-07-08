"""Proxy-side connection wrapping a Connection for proxy chain dispatch."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from acp.connection import Connection
    from acp.proxy.protocol import Proxy


class ProxySideConnection:
    """Wraps a Connection to dispatch proxy chain methods.

    Routes ``proxy/initialize`` and ``proxy/successor`` calls to a
    :class:`Proxy` implementation, forwarding all other methods to the
    wrapped :class:`Connection`.
    """

    def __init__(self, connection: Connection, proxy: Proxy) -> None:
        self._connection = connection
        self._proxy = proxy

    async def handle_proxy_method(
        self,
        method: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        """Dispatch a proxy chain method.

        Args:
            method: The JSON-RPC method name.
            params: The method parameters.

        Returns:
            The response from the proxy handler.

        Raises:
            ValueError: If the method is not a recognized proxy method.
        """
        from acp.proxy.constants import PROXY_INITIALIZE, PROXY_SUCCESSOR

        if method == PROXY_INITIALIZE:
            intercepted = self._proxy.proxy_initialize()
            return {"intercepted_methods": intercepted}
        if method == PROXY_SUCCESSOR:
            meta: dict[str, Any] = params.pop("_meta", {}) if isinstance(params, dict) else {}
            return await self._proxy.proxy_successor(
                method=params.get("method", ""), params=params, meta=meta
            )
        msg = f"Unknown proxy method: {method}"
        raise ValueError(msg)

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a request via the wrapped connection.

        Args:
            method: The JSON-RPC method name.
            params: Optional method parameters.

        Returns:
            The response from the connection.
        """
        result: dict[str, Any] = await self._connection.send_request(method, params or {})
        return result

    async def send_notification(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Send a notification via the wrapped connection."""
        await self._connection.send_notification(method, params or {})

    async def close(self) -> None:
        """Close the wrapped connection."""
        await self._connection.close()
