"""ACPClientAdapter — bridges ACPAgentAPI to the ACPClientProtocol interface.

This adapter makes :meth:`ACPAgentAPI.prompt` non-blocking by launching it
as a background asyncio task and routing session-update notifications to an
async queue that :meth:`stream_events` consumes.

When a Conductor is provided, prompt() routes through the proxy chain
via ``conductor._route_to_terminal()`` instead of ``api.prompt()``
directly, ensuring proxies (HookProxy, ContextInjectionProxy, etc.)
intercept and process messages.

Used by :class:`~agentpool.agents.acp_agent.turn.ACPTurn` via the
:class:`~agentpool.agents.acp_agent.turn.ACPClientProtocol` interface.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from acp.agent.acp_agent_api import ACPAgentAPI
    from acp.schema import ContentBlock, PromptResponse, SessionUpdate
    from agentpool.agents.acp_agent.client_handler import ACPClientHandler


class ACPClientAdapter:
    """Adapter wrapping :class:`ACPAgentAPI` for non-blocking ACP turn execution.

    Bridges the blocking ``ACPAgentAPI.prompt()`` (which returns
    ``PromptResponse`` only after all notifications) to the
    :class:`~agentpool.agents.acp_agent.turn.ACPClientProtocol` interface
    expected by :class:`~agentpool.agents.acp_agent.turn.ACPTurn`.

    When a Conductor is provided, the adapter routes ``session/prompt``
    through the proxy chain instead of calling ``api.prompt()`` directly.
    This ensures all configured proxies (HookProxy, ContextInjectionProxy,
    ToolProviderProxy) intercept and process messages.

    The adapter:
    - Fires prompt as a background task (fire-and-forget)
    - Routes session-update notifications to an async queue
    - Exposes ``stop_reason`` after the background task completes
    """

    def __init__(
        self,
        api: ACPAgentAPI,
        notification_source: ACPClientHandler | asyncio.Queue[SessionUpdate],
        conductor: Any | None = None,
    ) -> None:
        """Initialize the adapter.

        Args:
            api: The ACP agent API for sending prompts and retrieving messages.
            notification_source: Either an :class:`ACPClientHandler` that
                collects session updates or a raw ``asyncio.Queue`` of
                :class:`SessionUpdate` items.
            conductor: Optional Conductor for proxy chain routing. When
                provided and the method is intercepted, ``prompt()`` routes
                through ``conductor._route_to_terminal()`` instead of
                ``api.prompt()`` directly.
        """
        self._api = api
        self._notification_source = notification_source
        self._conductor = conductor
        self._queue: asyncio.Queue[SessionUpdate] | None = None
        self._prompt_task: asyncio.Task[Any] | None = None
        self._prompt_response: PromptResponse | None = None
        self._conductor_response: dict[str, Any] | None = None
        self._prompt_error: Exception | None = None
        self._collected_updates: list[SessionUpdate] = []

    async def prompt(self, session_id: str, content: list[ContentBlock]) -> None:
        """Send a prompt non-blocking — launches background task.

        When conductor is present and intercepts ``session/prompt``, routes
        through the proxy chain via ``conductor._route_to_terminal()``.
        Otherwise, falls back to ``api.prompt()`` directly.

        Args:
            session_id: The ACP session ID to prompt.
            content: List of ACP content blocks to send.

        Raises:
            RuntimeError: If a prompt is already in progress.
        """
        if self._prompt_task is not None and not self._prompt_task.done():
            raise RuntimeError("Prompt already in progress")

        # Initialize queue if not already created
        if self._queue is None:
            from agentpool.agents.acp_agent.client_handler import ACPClientHandler

            if isinstance(self._notification_source, ACPClientHandler):
                self._queue = asyncio.Queue(maxsize=1000)
                self._notification_source._stream_queue = self._queue
            else:
                self._queue = self._notification_source

        self._prompt_response = None
        self._conductor_response = None
        self._prompt_error = None
        self._collected_updates = []

        # Check if conductor should handle routing
        use_conductor = self._conductor is not None and self._conductor._should_intercept(
            "session/prompt"
        )

        if use_conductor:
            self._prompt_task = asyncio.create_task(self._run_conductor_prompt(session_id, content))
        else:
            self._prompt_task = asyncio.create_task(self._run_api_prompt(session_id, content))

    async def _run_conductor_prompt(
        self,
        session_id: str,
        content: list[ContentBlock],
    ) -> dict[str, Any]:
        """Route prompt through conductor's proxy chain.

        The conductor routes the request through all intercepting proxies
        (forward), sends to the terminal agent, then routes the response
        back through proxies in reverse order.

        Notifications arrive via the handler's stream queue during the
        blocking send_request call inside the conductor.
        """
        try:
            params: dict[str, Any] = {
                "sessionId": session_id,
                "prompt": content,
            }
            assert self._conductor is not None
            response: dict[str, Any] = await self._conductor._route_to_terminal(
                "session/prompt", params
            )
        except Exception as exc:
            self._prompt_error = exc
            raise
        self._conductor_response = response
        return response

    async def _run_api_prompt(
        self,
        session_id: str,
        content: list[ContentBlock],
    ) -> PromptResponse:
        """Call api.prompt() directly (no proxy chain)."""
        try:
            response = await self._api.prompt(session_id, content)
        except Exception as exc:
            self._prompt_error = exc
            raise
        self._prompt_response = response
        return response

    async def stream_events(self) -> AsyncIterator[SessionUpdate]:
        """Return an async iterator of session-update notifications.

        Yields :class:`SessionUpdate` items in order as they arrive from the
        ACP agent. The iterator signals completion when the background prompt
        task finishes. If the task raised an exception, it is propagated after
        draining remaining items.

        Yields:
            Session update notifications in order.

        Raises:
            RuntimeError: If :meth:`prompt` was not called first.
            Exception: If the background prompt task raised an exception.
        """
        if self._prompt_task is None:
            raise RuntimeError("No prompt in progress — call prompt() first")
        if self._queue is None:
            raise RuntimeError("Queue not initialized")

        prompt_task = self._prompt_task
        queue = self._queue

        while True:
            get_task = asyncio.create_task(queue.get())
            done, _pending = await asyncio.wait(
                [get_task, prompt_task],
                return_when=asyncio.FIRST_COMPLETED,
            )

            if get_task in done:
                item = get_task.result()
                self._collected_updates.append(item)
                yield item

            if prompt_task.done():
                if get_task not in done:
                    get_task.cancel()
                break

        # Drain remaining items after task completion
        while not queue.empty():
            item = queue.get_nowait()
            self._collected_updates.append(item)
            yield item

        # Propagate error if the background task failed
        if self._prompt_error is not None:
            raise self._prompt_error

    @property
    def stop_reason(self) -> str | None:
        """Return the stop reason after streaming completes.

        Extracts stop_reason from either:
        - The conductor response dict (when proxy chain was used)
        - The PromptResponse object (when api.prompt was used directly)

        Returns:
            The stop reason string, or ``None`` if the response has no stop reason.

        Raises:
            RuntimeError: If accessed before streaming completes.
        """
        if self._prompt_task is None or not self._prompt_task.done():
            raise RuntimeError("stop_reason not available until streaming completes")
        if self._prompt_error is not None:
            raise self._prompt_error
        # Conductor response path
        if self._conductor_response is not None:
            result = self._conductor_response.get("result", {})
            if isinstance(result, dict):
                return result.get("stopReason") or result.get("stop_reason")
            return None
        # API response path
        if self._prompt_response is not None:
            return self._prompt_response.stop_reason
        raise RuntimeError("Prompt completed without response or error")

    async def get_messages(self, session_id: str) -> list[SessionUpdate]:
        """Retrieve the full message history for a session.

        Returns the list of session updates collected during
        :meth:`stream_events`. Should only be called after the prompt
        completes.

        Args:
            session_id: The ACP session ID.

        Returns:
            A list of session updates representing the message history.
        """
        return list(self._collected_updates)
