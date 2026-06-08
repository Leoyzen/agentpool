"""Subagent catalog provider with debounced updates.

Provides a live catalog of available subagents from the AgentPool,
with debounced notification emission to avoid flooding clients.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import TYPE_CHECKING, Any

from agentpool.common_types import SupportsRunStream
from agentpool.log import get_logger


if TYPE_CHECKING:
    from acp.schema import SubagentInfo
    from agentpool import AgentPool

logger = get_logger(__name__)

CatalogUpdateCallback = Callable[[list[Any]], Awaitable[None]]


class SubagentCatalogProvider:
    """Manages the catalog of available subagents with debounced updates.

    Iterates over the AgentPool's agents and produces SubagentInfo entries
    for each agent that supports streaming (SupportsRunStream). Supports
    cycle detection by filtering out ancestor agent IDs.

    Updates are debounced so that rapid pool changes result in a single
    notification emission after the configured delay.
    """

    def __init__(self, pool: AgentPool[Any], debounce_ms: int = 500) -> None:
        """Initialize the catalog provider.

        Args:
            pool: The agent pool to source agents from.
            debounce_ms: Milliseconds to debounce update notifications.
        """
        self.pool = pool
        self.debounce_ms = debounce_ms
        self._pending_update: asyncio.Task[None] | None = None
        self._update_callbacks: list[CatalogUpdateCallback] = []
        self._notification_channels: list[Any] = []

    def register_update_callback(self, callback: CatalogUpdateCallback) -> None:
        """Register an async callback to be invoked when the catalog updates.

        Args:
            callback: Async function that receives the updated catalog.
        """
        self._update_callbacks.append(callback)

    def register_notification_channel(self, channel: Any) -> None:
        """Register a notification channel for session update emission.

        The channel must provide an async ``send_update(update)`` method.
        When the catalog updates after debounce, an
        ``AvailableSubagentsUpdate`` is sent to all registered channels.

        Args:
            channel: A notification channel (e.g., ``ACPNotifications``).
        """
        self._notification_channels.append(channel)

    def get_catalog(self, ancestor_agent_ids: set[str] | None = None) -> list[SubagentInfo]:
        """Build the current subagent catalog from the pool.

        Args:
            ancestor_agent_ids: Optional set of agent IDs to exclude from
                the catalog to prevent circular delegation.

        Returns:
            List of SubagentInfo for each available subagent.
        """
        from acp.schema import SubagentCapabilities, SubagentInfo

        if self.pool is None:
            return []

        result: list[SubagentInfo] = []
        for name, node in self.pool.all_agents.items():
            if not isinstance(node, SupportsRunStream):
                continue
            if ancestor_agent_ids and name in ancestor_agent_ids:
                continue

            title = getattr(node, "description", None) or name
            system_prompt = getattr(node, "system_prompt", None)
            if system_prompt is None:
                sys_prompts = getattr(node, "sys_prompts", None)
                if sys_prompts is not None:
                    prompts = getattr(sys_prompts, "prompts", None)
                    if prompts:
                        first_prompt = prompts[0]
                        system_prompt = (
                            first_prompt if isinstance(first_prompt, str) else str(first_prompt)
                        )
            description = str(system_prompt)[:200] if system_prompt is not None else None

            result.append(
                SubagentInfo(
                    subagent_id=name,
                    name=title,
                    description=description,
                    capabilities=SubagentCapabilities(
                        streaming=True,
                        tools=True,
                    ),
                )
            )
        return result

    async def notify_update(self) -> None:
        """Trigger a debounced catalog update notification.

        If an update is already pending, it is cancelled and a new one
        is scheduled. The actual emission happens after debounce_ms.
        """
        if self._pending_update is not None:
            self._pending_update.cancel()

        self._pending_update = asyncio.create_task(self._send_update_after_delay())

    async def _send_update_after_delay(self) -> None:
        """Wait for the debounce delay, then emit the updated catalog."""
        from acp.schema.session_updates import AvailableSubagentsUpdate

        try:
            await asyncio.sleep(self.debounce_ms / 1000)
            catalog = self.get_catalog()
            if not catalog:
                return

            update = AvailableSubagentsUpdate(available_subagents=catalog)
            for channel in list(self._notification_channels):
                try:
                    await channel.send_update(update)
                except Exception:
                    logger.exception("Notification channel send_update failed")

            for callback in list(self._update_callbacks):
                try:
                    result = callback(catalog)
                    if inspect.isawaitable(result):
                        await result
                except Exception:
                    logger.exception("Catalog update callback failed")
        finally:
            if self._pending_update is asyncio.current_task():
                self._pending_update = None
