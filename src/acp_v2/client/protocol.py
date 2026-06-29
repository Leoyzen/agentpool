"""ACP v2 client-side protocol interface."""

from __future__ import annotations

from typing import Any

from acp_v2.schema.notifications import SessionNotification


class Client:
    """ACP v2 Client protocol interface.

    Agents use this to send notifications and requests to the client.
    v2 differences from v1:
    - ``session_update()`` sends v2 ``SessionUpdate`` types
    - ``auth/logout`` is always available (no capability check)
    """

    async def session_update(self, params: SessionNotification) -> None:
        """Send a session/update notification to the client."""
        raise NotImplementedError

    async def session_request_permission(self, params: dict[str, Any]) -> dict[str, Any]:
        """Request user permission for a tool call."""
        raise NotImplementedError

    async def elicitation_create(self, params: dict[str, Any]) -> dict[str, Any]:
        """Request structured user input."""
        raise NotImplementedError

    async def elicitation_complete(self, params: dict[str, Any]) -> None:
        """Notify that elicitation is complete."""
        raise NotImplementedError

    async def send_request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request to the client."""
        raise NotImplementedError

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        """Send an extension notification to the client."""
        raise NotImplementedError
