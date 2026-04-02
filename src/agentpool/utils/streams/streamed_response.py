"""Convert between Codex and AgentPool types.

Provides converters for:
- Event conversion (Codex streaming events -> AgentPool events)
- MCP server configs (Native configs -> Codex types)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai import ModelResponse, RequestUsage

from agentpool.utils.streams.parts_manager import PartsManager


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from pydantic_ai import FinishReason

    from agentpool.agents.events import RichAgentStreamEvent


@dataclass(kw_only=True)
class StreamedResponse(ABC):
    """Streamed response from an LLM when calling a tool."""

    provider_name: str
    provider_response_id: str | None = field(default=None, init=False)
    provider_details: dict[str, Any] | None = field(default=None, init=False)
    finish_reason: FinishReason | None = field(default=None, init=False)
    _usage: RequestUsage = field(default_factory=RequestUsage, init=False)

    def __post_init__(self) -> None:
        self._parts_manager = PartsManager(self.provider_name)

    def __aiter__(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Stream the response as an async iterable of [`RichAgentStreamEvent`]."""
        return self._get_event_iterator()

    @abstractmethod
    async def _get_event_iterator(self) -> AsyncIterator[RichAgentStreamEvent[Any]]:
        """Return an async iterator of RichAgentStreamEvents.

        This method should be implemented by subclasses to translate the vendor-specific stream
        of events into agentpool-format events.

        It should use the `_parts_manager` to handle deltas, and should update the
        `_usage` attributes as it goes.
        """
        raise NotImplementedError
        # noinspection PyUnreachableCode
        yield

    def get(self) -> ModelResponse:
        """Build a ModelResponse from the data received from the stream so far."""
        return ModelResponse(
            parts=self._parts_manager.get_parts(),
            model_name=self.model_name,
            timestamp=self.timestamp,
            usage=self.usage(),
            provider_response_id=self.provider_response_id,
            provider_details=self.provider_details,
            finish_reason=self.finish_reason,
        )

    # TODO (v2): Make this a property
    def usage(self) -> RequestUsage:
        """Get the usage of the response so far.

        This will not be the final usage until the stream is exhausted.
        """
        return self._usage

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Get the model name of the response."""
        raise NotImplementedError

    @property
    @abstractmethod
    def timestamp(self) -> datetime:
        """Get the timestamp of the response."""
        raise NotImplementedError
