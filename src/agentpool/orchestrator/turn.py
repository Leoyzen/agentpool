"""Abstract base class for a single reactive cycle of agent execution."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from typing import Any

    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.messaging import ChatMessage


class Turn(ABC):
    """Abstract base class for a single reactive cycle of agent execution.

    A Turn encapsulates one complete reactive cycle: receiving input, executing
    through an agent (or agent team), and producing output events. Subclasses
    implement :meth:`execute` to drive the agent loop and yield stream events.

    After execution completes, :attr:`message_history` and :attr:`final_message`
    become available.
    """

    _message_history: list[ModelMessage] | None = None
    """Message history populated after execute() completes."""

    _final_message: ChatMessage[Any] | None = None
    """Final message populated after execute() completes."""

    @abstractmethod
    def execute(self) -> AsyncGenerator[RichAgentStreamEvent[Any]]:
        """Execute one reactive cycle of agent interaction.

        Yields stream events during execution (text deltas, tool calls,
        lifecycle notifications) and populates ``_message_history`` and
        ``_final_message`` before returning.
        """
        ...  # pragma: no cover

    @property
    def message_history(self) -> list[ModelMessage]:
        """Return the message history after execute() completes.

        Returns:
            The list of model messages from the completed turn.

        Raises:
            RuntimeError: If accessed before :meth:`execute` completes.
        """
        if self._message_history is None:
            raise RuntimeError("message_history is not available until execute() completes")
        return self._message_history

    @property
    def final_message(self) -> ChatMessage[Any]:
        """Return the final chat message after execute() completes.

        Returns:
            The final :class:`ChatMessage` produced by the turn.

        Raises:
            RuntimeError: If accessed before :meth:`execute` completes.
        """
        if self._final_message is None:
            raise RuntimeError("final_message is not available until execute() completes")
        return self._final_message
